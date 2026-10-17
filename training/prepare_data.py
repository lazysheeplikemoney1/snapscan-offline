"""Prepare CTC line crops from DEV photographs and human transcripts only.

The merged manifest's first 105 records are the three local DEV folders. The
reader is deliberately bounded: no remaining locked-test records are read.
Predictions locate labels; they never become labels. Fuzzy whole-line matches
are review candidates and cannot enter train/val without explicit approval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import difflib
import hashlib
from itertools import islice
import json
from pathlib import Path
import random
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MIN_LENGTH = 4
MAX_LENGTH = 96


def norm(text):
    return ''.join(text.split())


def load_dev_rows(path, limit=105):
    with Path(path).open(encoding='utf-8') as stream:
        rows = [json.loads(line) for line in islice(stream, limit)]
    if len(rows) != limit or any(r.get('split') != 'dev' for r in rows):
        raise ValueError('Bounded manifest prefix must contain exactly the expected DEV records')
    if len({r['image_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate image IDs in DEV manifest')
    return rows


def group_documents(rows, overlap_threshold=0.88):
    parents = {r['image_id']: r['image_id'] for r in rows}
    edges = []

    def find(item):
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def union(a, b, reason, score=None):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        parents[max(ra, rb)] = min(ra, rb)
        edge = {'a': a, 'b': b, 'reason': reason}
        if score is not None:
            edge['score'] = round(score, 6)
        edges.append(edge)

    seen = {}
    for row in rows:
        image_id = row['image_id']
        for field in ['source_group', 'source_group_id', 'duplicate_group', 'sha256']:
            if value := row.get(field):
                key = (field, str(value))
                if key in seen:
                    union(seen[key], image_id, field)
                seen[key] = image_id
        if row.get('duplicate_of') in parents:
            union(row['duplicate_of'], image_id, 'duplicate_of')
    texts = {r['image_id']: norm('\n'.join(t for t in r.get('gt_text', '').splitlines()
                                           if '[UNK]' not in t)) for r in rows}
    for index, a in enumerate(rows):
        ta = texts[a['image_id']]
        for b in rows[index + 1:]:
            tb = texts[b['image_id']]
            if (a.get('document_type') != b.get('document_type') or min(len(ta), len(tb)) < 80
                    or find(a['image_id']) == find(b['image_id'])):
                continue
            if ta == tb:
                union(a['image_id'], b['image_id'], 'identical_human_transcript', 1.0)
                continue
            shared = sum((Counter(ta) & Counter(tb)).values()) / min(len(ta), len(tb))
            if shared < overlap_threshold:
                continue
            matched = sum(x.size for x in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_matching_blocks())
            coverage = matched / min(len(ta), len(tb))
            if coverage >= overlap_threshold:
                union(a['image_id'], b['image_id'], 'high_human_text_overlap', coverage)
    return {key: 'doc_' + find(key).replace('.jpg', '') for key in parents}, edges


def assign_splits(rows, groups, val_fraction=0.2, seed=17):
    by_category = defaultdict(set)
    for row in rows:
        by_category[row.get('document_type', 'unknown')].add(groups[row['image_id']])
    result = {}
    rng = random.Random(seed)
    for category in sorted(by_category):
        group_ids = sorted(by_category[category])
        rng.shuffle(group_ids)
        nval = max(1, round(len(group_ids) * val_fraction)) if len(group_ids) > 1 else 0
        nval = min(nval, len(group_ids) - 1)
        for index, group_id in enumerate(group_ids):
            result[group_id] = 'val' if index < nval else 'train'
    return result


def exact_alignment(prediction, gt, min_length=MIN_LENGTH, max_length=MAX_LENGTH):
    predicted = norm(prediction)
    if not min_length <= len(predicted) <= max_length or '[UNK]' in prediction:
        return None
    sections = []
    active = []
    for line in gt.splitlines():
        if '[UNK]' in line:
            if active:
                sections.append('\n'.join(active))
                active = []
        else:
            active.append(line)
    if active:
        sections.append('\n'.join(active))
    hits = []
    gt_lines = [line for line in gt.splitlines() if '[UNK]' not in line]
    for section in sections:
        indices = [i for i, char in enumerate(section) if not char.isspace()]
        flat = ''.join(section[i] for i in indices)
        start = 0
        while (found := flat.find(predicted, start)) >= 0:
            raw = section[indices[found]:indices[found + len(predicted) - 1] + 1]
            label = raw.replace('\r', '').replace('\n', '').strip()
            hits.append({'text': label, 'alignment_method': 'exact_human_gt_line'
                         if any(norm(line) == predicted for line in gt_lines)
                         else 'exact_human_gt_substring'})
            start = found + 1
    return hits[0] if len(hits) == 1 else None


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def fuzzy_alignments(lines, gt, exact_indices=None):
    exact_indices = set(exact_indices or [])
    claimed = {norm(lines[index]['text']) for index in exact_indices}
    targets = [(i, text.strip(), norm(text)) for i, text in enumerate(gt.splitlines())
               if '[UNK]' not in text and 8 <= len(norm(text)) <= MAX_LENGTH and norm(text) not in claimed]
    candidates = []
    for index, proposal in enumerate(lines):
        if index in exact_indices:
            continue
        predicted = norm(proposal['text'])
        if not 8 <= len(predicted) <= MAX_LENGTH:
            continue
        ranked = []
        for gt_index, raw, target in targets:
            if abs(len(predicted) - len(target)) / max(len(predicted), len(target)) > 0.15:
                continue
            similarity = 1 - edit_distance(predicted, target) / max(len(predicted), len(target))
            ranked.append((similarity, gt_index, raw))
        ranked.sort(reverse=True)
        if not ranked or ranked[0][0] < 0.85 or ranked[0][0] >= 1.0:
            continue
        margin = ranked[0][0] - (ranked[1][0] if len(ranked) > 1 else 0)
        if margin < 0.10 - 1e-9:
            continue
        score, gt_index, label = ranked[0]
        candidates.append({'proposal_index': index, 'gt_line_index': gt_index, 'text': label,
                           'similarity': round(score, 6), 'runner_up_margin': round(margin, 6),
                           'alignment_method': 'fuzzy_whole_human_gt_line',
                           'annotation_status': 'review_required'})
    # A human line can supervise at most one proposal. Do not duplicate its label
    # onto adjacent or repeated detection boxes.
    chosen, used = [], set()
    for candidate in sorted(candidates, key=lambda x: (-x['similarity'], x['proposal_index'])):
        if candidate['gt_line_index'] not in used:
            used.add(candidate['gt_line_index'])
            chosen.append(candidate)
    return chosen


def scale_box(box, original_size, draft_size):
    return [[float(x) * original_size[0] / draft_size[0],
             float(y) * original_size[1] / draft_size[1]] for x, y in box]


def drop_seen_validation_labels(train, val):
    seen = {norm(row['text']) for row in train}
    return ([row for row in val if norm(row['text']) not in seen],
            [row for row in val if norm(row['text']) in seen])


def source_provenance(row):
    if row.get('split') != 'dev' or not row.get('image_path', '').replace('\\', '/').startswith('dev/'):
        raise ValueError('Only explicitly declared DEV sources may produce crops')
    return {'source_split': 'dev', 'source_image_id': row['image_id'],
            'source_image_path': row['image_path'], 'source_sha256': row['sha256']}


def crop_quad(image, box):
    import cv2
    import numpy as np
    from PIL import Image
    points = np.asarray(box, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError('Invalid quadrilateral')
    width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if width < 8 or height < 5:
        raise ValueError('Degenerate/tiny text crop')
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(points, target)
    cropped = cv2.warpPerspective(np.asarray(image), matrix, (width, height),
                                  borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if height / width >= 1.5:
        cropped = np.rot90(cropped)
    return Image.fromarray(cropped)


def in_roi(box, roi):
    if not roi:
        return True
    x0, y0, x1, y1 = roi
    return all(x0 - 8 <= x <= x1 + 8 and y0 - 8 <= y <= y1 + 8 for x, y in box)


def proposals_for_row(root, row, out, runtime):
    """Return boxes in EXIF-transposed ORIGINAL-image pixel coordinates.

    Draft transforms were recovered from the annotation agents' original commands
    and independently checked against RapidOCR main.py:89-122/255-284.
    111: ImageOps.exif_transpose(...).thumbnail((2000,2600)), no manual rescale.
    222: engine(str(original_path)), no prior resize/crop or manual rescale.
    RapidOCR restores its own internal resize before returning boxes.
    """
    from PIL import Image, ImageOps
    path = root / 'data/real' / row['image_path']
    image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    folder = Path(row['image_path']).parent.name
    draft_path = root / 'data/real/annotation_review' / folder / (Path(row['image_id']).stem + '_ocr.json')
    if folder in ['111', '222'] and draft_path.exists():
        draft = json.loads(draft_path.read_text(encoding='utf-8'))
        lines = draft.get('lines', draft.get('ocr_lines', []))
        if folder == '111':
            thumb = image.copy()
            thumb.thumbnail((2000, 2600))
            converted = [dict(line, box=scale_box(line['box'], image.size, thumb.size)) for line in lines]
            transform = {'input': 'EXIF-transposed PIL thumbnail', 'thumbnail_bounds': [2000, 2600],
                         'actual_input_size': list(thumb.size), 'original_size': list(image.size),
                         'formula': 'x*original_width/input_width, y*original_height/input_height',
                         'evidence': 'Original annotation command recovered from annotate_books agent; RapidOCR._get_origin_points source checked'}
        else:
            converted = lines
            transform = {'input': 'original file path passed directly to RapidOCR',
                         'original_size': list(image.size), 'formula': 'identity',
                         'evidence': 'Original annotation command confirmed by annotate_receipts agent; RapidOCR returns raw-image points'}
        return image, converted, {'kind': 'reused_annotation_detection',
                                  'path': draft_path.relative_to(root).as_posix(), 'transform': transform}
    cache_path = out / 'proposals' / (Path(row['image_id']).stem + '.json')
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding='utf-8'))
        if cached.get('source_sha256') == row['sha256'] and cached.get('coordinate_space') == 'original':
            return image, cached['lines'], cached['provenance']
    import cv2
    import numpy as np
    if not runtime:
        from rapidocr_onnxruntime import RapidOCR
        runtime.append(RapidOCR(det_limit_side_len=2000, intra_op_num_threads=2, inter_op_num_threads=2))
    roi = row.get('document_roi') or [0, 0, *image.size]
    x0, y0, x1, y1 = map(int, roi)
    result, _ = runtime[0](cv2.cvtColor(np.asarray(image.crop((x0, y0, x1, y1))), cv2.COLOR_RGB2BGR))
    converted = [{'box': [[float(x) + x0, float(y) + y0] for x, y in box],
                  'text': text, 'confidence': float(confidence)} for box, text, confidence in (result or [])]
    provenance = {'kind': 'new_dev_detection', 'detector': 'rapidocr_onnxruntime',
                  'transform': {'input': 'known document_roi crop of original EXIF-transposed image',
                                'roi': roi, 'formula': 'returned_x+x0, returned_y+y0; internal resize already restored'}}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({'source_sha256': row['sha256'], 'coordinate_space': 'original',
                                     'lines': converted, 'provenance': provenance}, ensure_ascii=False), encoding='utf-8')
    return image, converted, provenance


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def render_review_sheets(root, review, out):
    if not review:
        return []
    from PIL import Image, ImageDraw, ImageFont
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()
    sheets = []
    for page, offset in enumerate(range(0, len(review), 8), 1):
        batch = review[offset:offset + 8]
        sheet = Image.new('RGB', (1500, len(batch) * 180), 'white')
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(batch):
            y = index * 180
            crop = Image.open(root / item['crop_path']).convert('RGB')
            crop.thumbnail((1470, 90))
            sheet.paste(crop, (15, y + 5))
            caption = f"{item['candidate_id']} | {item['source_image_id']} | {item['split']} | GT: {item['text']}"
            for line_index, line in enumerate(textwrap.wrap(caption, 90)):
                draw.text((15, y + 100 + line_index * 23), line, fill='black', font=font)
        path = out / 'review' / f'contact_{page:03d}.jpg'
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path, quality=92)
        sheets.append(path.relative_to(root).as_posix())
    return sheets


def candidate_exclusion_reason(row, alignment, candidate_id, rejected):
    if candidate_id in rejected:
        return 'manual_visual_rejection'
    if row['document_type'] == '课件' and alignment['alignment_method'] == 'exact_human_gt_substring':
        return 'slide_substring_requires_full_visual_review'
    return None


def prepare(root=ROOT, out=None, seed=17, val_fraction=0.2, approved=None, max_review=100, rejected=None):
    root = Path(root).resolve()
    out = Path(out or root / 'training/data').resolve()
    if not out.is_relative_to(root / 'training'):
        raise ValueError('Generated data must stay inside project training/')
    out.mkdir(parents=True, exist_ok=True)
    rows = load_dev_rows(root / 'data/real/metadata.jsonl')
    for row in rows:
        path = root / 'data/real' / row['image_path']
        if not path.resolve().is_relative_to((root / 'data/real/dev').resolve()):
            raise ValueError('Non-DEV source path rejected')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('DEV source hash changed: ' + row['image_id'])
    groups, edges = group_documents(rows)
    splits = assign_splits(rows, groups, val_fraction, seed)
    approved_ids = set(json.loads(Path(approved).read_text(encoding='utf-8'))) if approved else set()
    rejected_ids = set(json.loads(Path(rejected).read_text(encoding='utf-8'))) if rejected else set()
    if approved_ids & rejected_ids:
        raise ValueError('A candidate cannot be both approved and rejected')
    train, val, review, excluded, transforms = [], [], [], [], []
    runtime = []
    used_hashes = set()
    from PIL import Image
    for row_index, row in enumerate(rows):
        image_id = row['image_id']
        if row['sha256'] in used_hashes:
            excluded.append({'source_image_id': image_id, 'reason': 'duplicate_image_hash'})
            continue
        used_hashes.add(row['sha256'])
        image, lines, provenance = proposals_for_row(root, row, out, runtime)
        transforms.append({'source_image_id': image_id, **provenance})
        split = splits[groups[image_id]]
        exact_indices = set()
        records = []
        for index, line in enumerate(lines):
            if line.get('confidence', 0) < 0.85:
                excluded.append({'source_image_id': image_id, 'proposal_index': index, 'reason': 'confidence_below_0.85'})
                continue
            if not in_roi(line['box'], row.get('document_roi')):
                excluded.append({'source_image_id': image_id, 'proposal_index': index, 'reason': 'outside_annotation_roi'})
                continue
            alignment = exact_alignment(line['text'], row['gt_text'])
            if alignment:
                exact_indices.add(index)
                records.append((index, alignment))
        fuzzy = fuzzy_alignments(lines, row['gt_text'], exact_indices)
        for candidate in fuzzy:
            index = candidate['proposal_index']
            if lines[index].get('confidence', 0) < 0.85 or not in_roi(lines[index]['box'], row.get('document_roi')):
                continue
            records.append((index, candidate))
        matched_indices = {index for index, _ in records}
        for index, line in enumerate(lines):
            if index not in matched_indices:
                excluded.append({'source_image_id': image_id, 'proposal_index': index, 'baseline_text': line['text'],
                                 'reason': 'no_unambiguous_supported_human_gt_alignment_or_filtered'})
        for index, alignment in records:
            line = lines[index]
            is_review = alignment.get('annotation_status') == 'review_required'
            fingerprint = json.dumps([row['sha256'], line['box'], alignment['text']], ensure_ascii=False)
            candidate_id = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]
            exclusion_reason = candidate_exclusion_reason(row, alignment, candidate_id, rejected_ids)
            if exclusion_reason:
                excluded.append({'source_image_id': image_id, 'candidate_id': candidate_id, 'reason': exclusion_reason})
                continue
            accepted_review = candidate_id in approved_ids
            if is_review and not accepted_review and len(review) >= max_review:
                excluded.append({'source_image_id': image_id, 'proposal_index': index, 'reason': 'review_budget_limit'})
                continue
            subdir = 'review' if is_review else 'crops'
            crop_path = out / subdir / f'{Path(image_id).stem}_{index:03d}_{candidate_id}.png'
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                crop = crop_quad(image, line['box'])
            except ValueError as error:
                excluded.append({'source_image_id': image_id, 'proposal_index': index, 'reason': str(error)})
                continue
            crop.save(crop_path)
            record = {'crop_path': crop_path.relative_to(root).as_posix(), 'text': alignment['text'],
                      'source_image_id': image_id, 'group_id': groups[image_id], 'split': split,
                      'document_type': row['document_type'], 'alignment_method': alignment['alignment_method'],
                      'baseline_text': line['text'], 'box': line['box'], 'source_sha256': row['sha256'],
                      'candidate_id': candidate_id, 'confidence': line.get('confidence'),
                      'label_source': 'human_gt_only', **source_provenance(row)}
            if is_review:
                record.update({key: alignment[key] for key in ['similarity', 'runner_up_margin', 'gt_line_index']})
                record['annotation_status'] = 'manually_approved' if accepted_review else 'review_required'
                if not accepted_review:
                    review.append(record)
                    continue
            else:
                record['annotation_status'] = 'exact_gt_alignment'
            (train if split == 'train' else val).append(record)
        print(json.dumps({'progress': row_index + 1, 'image_id': image_id, 'proposals': len(lines),
                          'train': len(train), 'val': len(val), 'review_required': len(review)}), flush=True)
    val, repeats = drop_seen_validation_labels(train, val)
    excluded.extend({'source_image_id': row['source_image_id'], 'candidate_id': row['candidate_id'],
                     'reason': 'validation_label_already_seen_in_train'} for row in repeats)
    if not train or not val:
        raise ValueError('Empty training/validation split after conservative filtering')
    if {row['group_id'] for row in train} & {row['group_id'] for row in val}:
        raise AssertionError('Group leakage')
    if {row['source_sha256'] for row in train} & {row['source_sha256'] for row in val}:
        raise AssertionError('Image hash leakage')
    if {norm(row['text']) for row in train} & {norm(row['text']) for row in val}:
        raise AssertionError('Exact label overlap after validation filtering')
    for name, data in [('train', train), ('val', val), ('review_required', review), ('exclusions', excluded)]:
        write_jsonl(out / f'{name}.jsonl', data)
    sheets = render_review_sheets(root, review, out)
    members = defaultdict(list)
    for image_id, group_id in groups.items():
        members[group_id].append(image_id)
    group_list = [{'group_id': group_id, 'split': splits[group_id], 'source_image_ids': sorted(ids)}
                  for group_id, ids in sorted(members.items())]
    report = {'dev_records_read': len(rows), 'test_records_read': 0, 'test_images_processed': 0,
              'read_policy': 'islice(first105) with all split=dev assertion; no following test records read',
              'seed': seed, 'val_fraction': val_fraction, 'unique_dev_image_hashes': len(used_hashes),
              'document_groups': group_list, 'group_edges': edges, 'coordinate_provenance': transforms,
              'train_lines': len(train), 'val_lines': len(val), 'review_required_lines': len(review),
              'approved_review_lines': sum(r['annotation_status'] == 'manually_approved' for r in train + val),
              'train_characters': sum(len(r['text']) for r in train), 'val_characters': sum(len(r['text']) for r in val),
              'train_categories': dict(Counter(r['document_type'] for r in train)),
              'val_categories': dict(Counter(r['document_type'] for r in val)),
              'exclusion_counts': dict(Counter(r['reason'] for r in excluded)),
              'review_contact_sheets': sheets,
              'checks': {'group_leakage': False, 'image_hash_leakage': False, 'exact_label_overlap': False,
                         'fuzzy_unreviewed_in_train_val': False, 'labels_are_human_gt': True},
              'limitations': ['Internal DEV holdout, not locked final test.',
                             'Exact matching alone selects baseline-correct lines; review fuzzy candidates before model selection.',
                             'Only boxes surviving OCR draft filtering are available for111/222; omitted text is not silently labeled.',
                             'Documents grouped by known provenance/hash/high GT overlap; common source book/course remains shared domain.']}
    (out / 'data_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--val-fraction', type=float, default=0.2)
    parser.add_argument('--approved-review', type=Path)
    parser.add_argument('--rejected-candidates', type=Path)
    parser.add_argument('--max-review', type=int, default=100)
    args = parser.parse_args()
    report = prepare(seed=args.seed, val_fraction=args.val_fraction,
                     approved=args.approved_review, max_review=args.max_review, rejected=args.rejected_candidates)
    print(json.dumps({key: report[key] for key in ['train_lines', 'val_lines', 'review_required_lines',
                                                  'approved_review_lines', 'checks']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
