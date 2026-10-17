"""Fine-tune the existing PP-OCR CTC head; never ingest the final test set."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import random
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def encode_text(text, charset):
    index = {c: i for i, c in enumerate(charset) if i != 0}
    if not text or '[UNK]' in text:
        raise ValueError('Empty/unknown ground truth cannot be trained')
    missing = sorted(set(text) - index.keys())
    if missing:
        raise ValueError('Characters outside pretrained vocabulary: ' + repr(missing))
    return [index[c] for c in text]


def min_ctc_steps(ids):
    return len(ids) + sum(a == b for a, b in zip(ids, ids[1:]))


def ctc_decode(ids, charset):
    out, previous = [], -1
    for value in ids:
        value = int(value)
        if value != 0 and value != previous:
            out.append(charset[value])
        previous = value
    return ''.join(out)


def validate_rows(train, val, dev_ids=None):
    if not train or not val:
        raise ValueError('Both training and validation must contain samples')
    for split, rows in [('train', train), ('val', val)]:
        for r in rows:
            if r.get('split') != split or r.get('source_split') != 'dev':
                raise ValueError('Only explicitly split development samples are allowed')
            if dev_ids is not None and r.get('source_image_id') not in dev_ids:
                raise ValueError('Source image is absent from the official development registry')
            if not r.get('group_id') or not r.get('source_image_id'):
                raise ValueError('Missing document grouping')
            if not r.get('text') or '[UNK]' in r['text']:
                raise ValueError('Empty or unknown labels are forbidden')
    for field in ['source_image_id', 'group_id']:
        if {r[field] for r in train} & {r[field] for r in val}:
            raise ValueError('Training/validation leakage in ' + field)


def train_one_batch(layer, optimizer, features, lengths, labels, anchor=None, anchor_strength=0.0):
    import torch
    optimizer.zero_grad(set_to_none=True)
    logits = layer(features)
    target = torch.tensor([c for seq in labels for c in seq], dtype=torch.long, device=features.device)
    input_lengths = torch.tensor(lengths, dtype=torch.long)
    target_lengths = torch.tensor([len(s) for s in labels], dtype=torch.long)
    loss = torch.nn.functional.ctc_loss(logits.log_softmax(-1).transpose(0, 1), target,
                                         input_lengths, target_lengths, blank=0,
                                         reduction='mean', zero_infinity=False)
    if anchor is not None:
        loss = loss + anchor_strength * (
            (layer.weight - anchor[0]).square().sum() + (layer.bias - anchor[1]).square().sum())
    if not torch.isfinite(loss):
        raise RuntimeError('Non-finite CTC loss; check target lengths and alignment')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(layer.parameters(), 5.0, error_if_nonfinite=True)
    optimizer.step()
    return float(loss.detach().cpu())


def load_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def extract_features(rows, session, charset, model_hash, cache_dir):
    from training.onnx_head import preprocess_crop
    samples, excluded = [], []
    input_name = session.get_inputs()[0].name
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows):
        try:
            ids = encode_text(row['text'], charset)
            crop = (ROOT / row['crop_path']).resolve()
            if not crop.is_relative_to(ROOT / 'training' / 'data'):
                raise ValueError('Crop path must be under training/data')
            crop_hash = sha256(crop)
            key = hashlib.sha256((model_hash + crop_hash + sha256(ROOT/'training/onnx_head.py')).encode()).hexdigest()
            cached = cache_dir / (key + '.npy')
            if cached.exists():
                feature = np.load(cached, allow_pickle=False)
            else:
                img = cv2.imdecode(np.fromfile(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError('Unreadable crop')
                tensor = preprocess_crop(img)
                if tensor.shape[-1] > 4096:
                    raise ValueError('Text line wider than conservative 4096-pixel limit')
                feature = session.run(None, {input_name: tensor})[0][0]
                np.save(cached, feature, allow_pickle=False)
            if not np.isfinite(feature).all() or feature.ndim != 2:
                raise ValueError('Invalid cached features')
            if len(feature) < min_ctc_steps(ids):
                raise ValueError('CTC target cannot fit available timesteps')
            samples.append({'row': row, 'features': feature.astype(np.float32), 'ids': ids,
                            'crop_sha256': crop_hash})
        except ValueError as exc:
            excluded.append({'source_image_id': row['source_image_id'], 'crop_path': row['crop_path'],
                             'reason': str(exc), 'split': row['split']})
        if (i + 1) % 100 == 0:
            print(json.dumps({'phase': 'features', 'split': row['split'], 'processed': i+1,
                              'total': len(rows), 'accepted': len(samples)}), flush=True)
    return samples, excluded


def collate(samples, device):
    import torch
    lengths = [len(s['features']) for s in samples]
    array = np.zeros((len(samples), max(lengths), samples[0]['features'].shape[-1]), dtype=np.float32)
    for i, sample in enumerate(samples):
        array[i, :lengths[i]] = sample['features']
    return torch.from_numpy(array).to(device), lengths, [s['ids'] for s in samples]


def evaluate(layer, samples, charset, batch_size, device):
    import torch
    from evaluation.tools.metrics import corpus_cer, normalize_text
    predictions = []
    layer.eval()
    ordered = sorted(samples, key=lambda s: len(s['features']))
    with torch.no_grad():
        for start in range(0, len(ordered), batch_size):
            batch = ordered[start:start+batch_size]
            features, lengths, _ = collate(batch, device)
            ids = layer(features).argmax(-1).cpu().numpy()
            for row, values, length in zip(batch, ids, lengths):
                pred = ctc_decode(values[:length], charset)
                predictions.append({'crop_path': row['row']['crop_path'], 'source_image_id': row['row']['source_image_id'],
                                    'document_type': row['row']['document_type'], 'pred': pred, 'gt': row['row']['text']})
    metrics = corpus_cer([(r['pred'], r['gt']) for r in predictions])
    metrics.update(n_lines=len(predictions), exact_line_accuracy=sum(normalize_text(r['pred']) == normalize_text(r['gt'])
                  for r in predictions) / len(predictions))
    metrics['by_document_type'] = {
        kind: corpus_cer([(r['pred'], r['gt']) for r in predictions if r['document_type'] == kind])
        for kind in sorted({r['document_type'] for r in predictions})}
    return metrics, predictions


def verify_export(export_path, layer, samples, threads, device):
    import torch
    import onnxruntime as ort
    from training.onnx_head import preprocess_crop
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(export_path), sess_options=options, providers=['CPUExecutionProvider'])
    errors = []
    for sample in samples[:8]:
        img = cv2.imdecode(np.fromfile(ROOT / sample['row']['crop_path'], dtype=np.uint8), cv2.IMREAD_COLOR)
        got = session.run(None, {session.get_inputs()[0].name: preprocess_crop(img)})[0]
        with torch.no_grad():
            expected = layer(torch.from_numpy(sample['features'][None]).to(device)).softmax(-1).cpu().numpy()
        np.testing.assert_allclose(got, expected, atol=2e-4, rtol=2e-4)
        errors.append(float(np.max(np.abs(got - expected))))
    return {'checked_crops': len(errors), 'max_probability_error': max(errors), 'passed': True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', default='backend/models/fp32/rec_fp32.onnx')
    ap.add_argument('--data', default='training/data')
    ap.add_argument('--out', default='training/runs/rec_head_v1')
    ap.add_argument('--epochs', type=int, default=20)
    ap.add_argument('--batch-size', type=int, default=16)
    ap.add_argument('--lr', type=float, default=5e-5)
    ap.add_argument('--patience', type=int, default=5)
    ap.add_argument('--seed', type=int, default=20261015)
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    ap.add_argument('--anchor-strength', type=float, default=0.001)
    args = ap.parse_args()
    import torch
    from training.onnx_head import inspect_head, create_feature_session, load_charset, export_head
    from training.prepare_data import load_dev_rows, group_documents
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    torch.backends.cuda.matmul.allow_tf32 = False
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('Requested GPU training but CUDA is unavailable')
    if args.epochs < 1 or args.batch_size < 1:
        raise ValueError('epochs and batch-size must be positive')
    device = torch.device(args.device)
    model_path, data_dir, out = ROOT / args.model, ROOT / args.data, ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'training_report.json').exists():
        raise FileExistsError('Finished run exists; use a new --out directory')
    lock = ROOT / 'data/real/test/test_lock.json'
    lock_hash = sha256(lock)
    metadata_hash = sha256(ROOT / 'data/real/metadata_template.jsonl')
    manifest_hashes = {split: sha256(data_dir/(split+'.jsonl')) for split in ['train', 'val']}
    rows = {split: load_rows(data_dir / (split + '.jsonl')) for split in ['train', 'val']}
    dev_rows = load_dev_rows(ROOT/'data/real/metadata_template.jsonl', limit=105)
    registry = {r['image_id']: r for r in dev_rows}
    authoritative_groups, _ = group_documents(dev_rows)
    validate_rows(rows['train'], rows['val'], dev_ids=set(registry))
    for records in rows.values():
        for row in records:
            if row['group_id'] != authoritative_groups[row['source_image_id']]:
                raise ValueError('Manifest group differs from independently reconstructed document group')
            source = registry[row['source_image_id']]
            if row.get('source_sha256') != source['sha256']:
                raise ValueError('Manifest does not carry the verified development source hash')
    for image_id in {r['source_image_id'] for records in rows.values() for r in records}:
        source = registry[image_id]
        if sha256(ROOT/'data/real'/source['image_path']) != source['sha256']:
            raise ValueError('Development source photo changed: ' + image_id)
    model_hash = sha256(model_path)
    info = inspect_head(model_path)
    charset = load_charset(model_path)
    session = create_feature_session(model_path, threads=args.threads)
    data, exclusions = {}, []
    for split in ['train', 'val']:
        data[split], excluded = extract_features(rows[split], session, charset, model_hash, ROOT/'training/cache/features')
        exclusions.extend(excluded)
    write_json(out/'feature_exclusions.json', exclusions)
    if not data['train'] or not data['val']:
        raise ValueError('No usable samples after feature/label validation')
    validate_rows([s['row'] for s in data['train']], [s['row'] for s in data['val']], dev_ids=set(registry))
    layer = torch.nn.Linear(info['weight'].shape[0], info['weight'].shape[1]).to(device)
    with torch.no_grad():
        layer.weight.copy_(torch.from_numpy(info['weight'].T.copy()).to(device))
        layer.bias.copy_(torch.from_numpy(info['bias'].copy()).to(device))
    anchor = (layer.weight.detach().clone(), layer.bias.detach().clone())
    baseline, baseline_predictions = evaluate(layer, data['val'], charset, args.batch_size, device)
    write_json(out/'baseline_predictions.json', baseline_predictions)
    print(json.dumps({'phase': 'baseline', 'train_lines': len(data['train']), 'val': baseline}), flush=True)
    optimizer = torch.optim.AdamW(layer.parameters(), lr=args.lr, weight_decay=0.)
    history, best, best_epoch, stale = [], float('inf'), 0, 0
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        layer.train()
        ordered = sorted(data['train'], key=lambda s: len(s['features']))
        batches = [ordered[i:i+args.batch_size] for i in range(0, len(ordered), args.batch_size)]
        random.shuffle(batches)
        losses = []
        for batch in batches:
            features, lengths, labels = collate(batch, device)
            losses.append(train_one_batch(layer, optimizer, features, lengths, labels, anchor, args.anchor_strength))
        metrics, predictions = evaluate(layer, data['val'], charset, args.batch_size, device)
        record = {'epoch': epoch, 'train_ctc_plus_anchor_loss': float(np.mean(losses)),
                  'val': metrics, 'elapsed_seconds': round(time.perf_counter()-started, 2)}
        history.append(record)
        write_json(out/'history.json', history)
        torch.save({'epoch': epoch, 'state_dict': layer.state_dict(), 'optimizer': optimizer.state_dict(),
                    'args': vars(args), 'model_sha256': model_hash}, out/'last.pt')
        if metrics['corpus_cer'] < best:
            best, best_epoch, stale = metrics['corpus_cer'], epoch, 0
            torch.save({'epoch': epoch, 'state_dict': layer.state_dict(), 'args': vars(args),
                        'model_sha256': model_hash}, out/'best.pt')
            write_json(out/'best_predictions.json', predictions)
        else:
            stale += 1
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if stale >= args.patience:
            break
    checkpoint = torch.load(out/'best.pt', map_location=device, weights_only=True)
    layer.load_state_dict(checkpoint['state_dict'])
    weight = layer.weight.detach().cpu().numpy().T.copy()
    bias = layer.bias.detach().cpu().numpy().copy()
    delta = float(np.linalg.norm(weight - info['weight']))
    if not delta > 0:
        raise AssertionError('Training did not change model weights')
    exported = out/'rec_finetuned.onnx'
    export_head(model_path, weight, bias, exported)
    export_check = verify_export(exported, layer, data['val'], args.threads, device)
    best_metrics, _ = evaluate(layer, data['val'], charset, args.batch_size, device)
    if sha256(lock) != lock_hash or sha256(ROOT/'data/real/metadata_template.jsonl') != metadata_hash:
        raise AssertionError('Locked data or original metadata changed during training')
    if sha256(model_path) != model_hash:
        raise AssertionError('Original pretrained model changed during training')
    if any(sha256(data_dir/(split+'.jsonl')) != digest for split, digest in manifest_hashes.items()):
        raise AssertionError('Dataset manifest changed during training')
    crop_manifest = []
    for split, samples in data.items():
        for sample in samples:
            row = sample['row']
            if sha256(ROOT/row['crop_path']) != sample['crop_sha256']:
                raise AssertionError('Crop changed during training: ' + row['crop_path'])
            crop_manifest.append({'crop_path': row['crop_path'], 'crop_sha256': sample['crop_sha256'],
                                  'split': split, 'source_image_id': row['source_image_id']})
    write_json(out/'crop_manifest.json', crop_manifest)
    environment = {package: importlib.metadata.version(package)
                   for package in ['torch', 'onnx', 'onnxruntime', 'numpy', 'rapidocr-onnxruntime']}
    report = {
        'status': 'completed', 'training_type': 'pretrained_CTC_recognition_head_finetuning',
        'backbone_frozen': True, 'detector_trained': False, 'full_network_training': False,
        'args': vars(args), 'environment': environment,
        'device': torch.cuda.get_device_name(0) if args.device == 'cuda' else 'CPU',
        'trainable_parameters': sum(p.numel() for p in layer.parameters()),
        'splits': {split: {'lines': len(data[split]), 'source_photos': len({s['row']['source_image_id'] for s in data[split]}),
                          'groups': len({s['row']['group_id'] for s in data[split]}),
                          'by_type': dict(Counter(s['row']['document_type'] for s in data[split]))}
                   for split in ['train', 'val']},
        'baseline_validation': baseline, 'best_validation': best_metrics,
        'best_epoch': best_epoch, 'epochs_run': len(history), 'history': history,
        'validation_improved': best_metrics['corpus_cer'] < baseline['corpus_cer'],
        'deployment_eligible': False,
        'deployment_gate': 'Requires whole-page representative evaluation and application integration checks.',
        'automatic_app_model_replacement': False, 'final_test_evaluated': False,
        'test_lock_sha256_before_and_after': lock_hash, 'metadata_sha256': metadata_hash,
        'dataset_manifests_sha256_before_and_after': manifest_hashes,
        'crop_manifest_sha256': sha256(out/'crop_manifest.json'),
        'base_model_sha256': model_hash, 'trained_model_sha256': sha256(exported),
        'weight_change_l2': delta, 'export_verification': export_check,
        'training_seconds': round(time.perf_counter()-started, 2),
        'limitations': ['Internal development holdout of conservatively aligned text crops, not final-test accuracy.',
                       'Detector proposals and alignment filtering bias the sample toward detectable/readable text.',
                       'Model contains adaptation to private user-provided documents; local research artifact.']
    }
    write_json(out/'training_report.json', report)
    print(json.dumps({'phase': 'completed', 'report': str(out/'training_report.json'),
                      'baseline_cer': baseline['corpus_cer'], 'trained_cer': best_metrics['corpus_cer'],
                      'best_epoch': best_epoch, 'export_check': export_check}), flush=True)


if __name__ == '__main__':
    main()
