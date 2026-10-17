"""Offline observed paper boundaries; confidence is heuristic, not a probability."""
import cv2
import numpy as np


def _points(pts):
    try:
        points = np.asarray(pts, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("四角必须是四组有限坐标") from exc
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("四角必须是四组有限坐标")
    if len(np.unique(points, axis=0)) != 4:
        raise ValueError("四角不能重复")
    return points.copy()


def order_points(pts):
    """Sort a convex point set to TL/TR/BR/BL, using each vertex once.

    Validate connected manual UI points BEFORE sorting; sorting hides crossings.
    Equal sums are resolved by y then x, including symmetric diamonds.
    """
    points = _points(pts)
    center = points.mean(axis=0)
    ordered = points[np.argsort(np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0]))]
    if not cv2.isContourConvex(ordered):
        raise ValueError("四角必须形成凸四边形")
    start = np.lexsort((ordered[:, 0], ordered[:, 1], ordered.sum(axis=1)))[0]
    return np.roll(ordered, -int(start), axis=0).astype(np.float32)


def validate_quad(quad, image_shape=None):
    """Validate a connected TL/TR/BR/BL ring WITHOUT reordering.

    Returns float32 points or raises ValueError. image_shape is (h,w) or img.shape.
    Manual API input must pass here before four_point_transform/order_points.
    """
    points = _points(quad)
    if not cv2.isContourConvex(points) or cv2.contourArea(points, oriented=True) <= 0:
        raise ValueError("四角需按左上、右上、右下、左下连续排列，不能自交")
    sides = np.roll(points, -1, axis=0)-points
    lengths = np.linalg.norm(sides, axis=1)
    if np.min(lengths) < 8 or cv2.contourArea(points) < 64:
        raise ValueError("所选四边形尺寸或面积过小")
    turns = sides[:, 0]*np.roll(sides[:, 1], -1)-sides[:, 1]*np.roll(sides[:, 0], -1)
    if np.any(turns/(lengths*np.roll(lengths, -1)) < .04):
        raise ValueError("所选四角过于扁平，无法稳定矫正")
    if image_shape is not None:
        try:
            height, width = int(image_shape[0]), int(image_shape[1])
        except (TypeError, IndexError, ValueError) as exc:
            raise ValueError("图像尺寸无效") from exc
        if height < 2 or width < 2:
            raise ValueError("图像尺寸无效")
        if (np.any(points[:, 0] < 0) or np.any(points[:, 1] < 0)
                or np.any(points[:, 0] > width-1) or np.any(points[:, 1] > height-1)):
            raise ValueError("四角不能超出图像边界")
        if cv2.contourArea(points) < .001*height*width:
            raise ValueError("所选四边形面积过小")
    return points


def _gray_image(img):
    if not isinstance(img, np.ndarray) or img.ndim not in (2, 3) or min(img.shape[:2]) < 16:
        return None
    if img.dtype != np.uint8:
        return None
    if img.ndim == 2:
        return img
    if img.shape[2] not in (3, 4):
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _candidates(mask, shape):
    height, width = shape
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:100]:
        area = cv2.contourArea(contour)
        if not .02*height*width <= area <= .96*height*width:
            continue
        perimeter = cv2.arcLength(contour, True)
        for epsilon in (.01, .015, .02, .03):
            approximation = cv2.approxPolyDP(contour, epsilon*perimeter, True)
            if len(approximation) != 4:
                continue
            try:
                quad = validate_quad(order_points(approximation.reshape(4, 2)), shape)
            except ValueError:
                continue
            agreement = area/cv2.contourArea(quad)
            if .86 <= agreement <= 1.12:
                yield quad, min(agreement, 1/agreement)
                break


def _score_candidate(gray, distance, all_edges, quad, agreement):
    height, width = gray.shape
    ratio = cv2.contourArea(quad)/(height*width)
    if not .02 <= ratio <= .94:
        return None
    sides = np.roll(quad, -1, axis=0)-quad
    lengths = np.linalg.norm(sides, axis=1)
    if max(lengths)/min(lengths) > 22:
        return None
    if max(lengths[0], lengths[2])/min(lengths[0], lengths[2]) > 3 or max(lengths[1], lengths[3])/min(lengths[1], lengths[3]) > 3:
        return None
    turns = sides[:, 0]*np.roll(sides[:, 1], -1)-sides[:, 1]*np.roll(sides[:, 0], -1)
    if np.min(turns/(lengths*np.roll(lengths, -1))) < .25:
        return None
    near_frame = ((quad[:, 0] < 3) | (quad[:, 0] > width-4) | (quad[:, 1] < 3) | (quad[:, 1] > height-4))
    if np.count_nonzero(near_frame) >= 3:
        return None
    support, contrasts, margin_values = [], [], []
    inset = max(3.0, min(8.0, float(min(lengths))*.04))
    for index, edge in enumerate(sides):
        samples = quad[index]+np.linspace(.05, .95, 80)[:, None]*edge
        normal = np.array([-edge[1], edge[0]], np.float32)/lengths[index]
        inner = np.rint(samples+normal*inset).astype(int)
        outer = np.rint(samples-normal*inset).astype(int)
        valid = ((inner[:, 0] >= 0) & (inner[:, 0] < width) & (inner[:, 1] >= 0) & (inner[:, 1] < height)
                 & (outer[:, 0] >= 0) & (outer[:, 0] < width) & (outer[:, 1] >= 0) & (outer[:, 1] < height))
        sampled = np.rint(samples).astype(int)
        support.append(float(np.mean(distance[sampled[:, 1], sampled[:, 0]] <= 2.8)))
        if valid.sum() >= 24:
            inside_values = gray[inner[valid, 1], inner[valid, 0]]
            outside_values = gray[outer[valid, 1], outer[valid, 0]]
            margin_values.append(float(np.median(inside_values)))
            contrasts.append(abs(float(np.median(inside_values))-float(np.median(outside_values))))
    if len(contrasts) < 3 or sum(value >= 6 for value in contrasts) < 3 or np.median(contrasts) < 7:
        return None
    if min(support) < .35 or np.mean(support) < .65 or np.median(margin_values) < 135:
        return None
    x, y, crop_width, crop_height = cv2.boundingRect(quad)
    local = np.zeros((crop_height, crop_width), np.uint8)
    cv2.fillConvexPoly(local, np.rint(quad-[x, y]).astype(np.int32), 255)
    inside = cv2.erode(local, np.ones((7, 7), np.uint8)) > 0
    values = gray[y:y+crop_height, x:x+crop_width][inside]
    if values.size < 64:
        return None
    bright_fraction = float(np.mean(values >= 130))
    edge_density = float(np.mean(all_edges[y:y+crop_height, x:x+crop_width][inside] > 0))
    if bright_fraction < .70 or np.median(values) < 145 or edge_density > .28:
        return None
    mean_support, contrast = float(np.mean(support)), float(np.median(contrasts))
    score = (.40*mean_support+.22*min(contrast/35, 1)+.16*bright_fraction
             +.12*agreement+.10*min(np.sqrt(ratio/.5), 1))
    return {"score": float(score), "area_ratio": float(ratio), "boundary_support": mean_support,
            "boundary_contrast": contrast, "interior_bright_fraction": bright_fraction,
            "interior_edge_density": edge_density}


def paper_region_mask(img, chroma_limit=10, color_center=None):
    """Diagnostic neutral-paper mask, using portable Lab + morphology operations.

    This is a *proposal*, not a document decision: neutral glare can also pass.
    Closing fills text strokes; opening removes isolated color/noise specks.
    Values use OpenCV's uint8 Lab convention (a=b=128 for neutral pixels).
    Optional color_center=(a,b) measures relative paper-color distance under
    tinted lighting; it does not widen the absolute-neutral threshold.
    """
    gray = _gray_image(img)
    if gray is None or img.ndim != 3:
        return None
    lab = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2LAB).astype(np.float32)
    center = np.array([128, 128] if color_center is None else color_center, np.float32)
    chroma = np.linalg.norm(lab[:, :, 1:]-center, axis=2)
    mask = ((gray >= 100) & (chroma <= chroma_limit)).astype(np.uint8)*255
    size = min(15, max(5, int(round(min(gray.shape)*.015)) | 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def _frame_sides(quad, shape):
    height, width = shape[:2]
    sides = {}
    for index in range(4):
        pair = quad[[index, (index+1) % 4]]
        for name, axis, value in (("top", 1, 0), ("right", 0, width-1),
                                  ("bottom", 1, height-1), ("left", 0, 0)):
            if np.max(np.abs(pair[:, axis]-value)) <= 1.5:
                sides[index] = name
                break
    return sides


def _content_cut_sides(gray, quad):
    """Near-frame sides ending in a broad dark screen/bezel, not recovered edges.

    A side must be within the outer 7% and its outward strip mostly dark. These
    cuts describe only visible content. Three/four leave too little independent
    side evidence for the extra fallback. The normal complete-paper path must
    still accept a genuine large sheet on a dark desk with the same geometry.
    """
    height, width = gray.shape
    physical = _frame_sides(quad, gray.shape)
    found = {}
    for index in range(4):
        if index in physical:
            continue
        pair = quad[[index, (index+1) % 4]]
        possible = []
        for name, axis, value, dimension in (("top", 1, 0, height), ("right", 0, width-1, width),
                                              ("bottom", 1, height-1, height), ("left", 0, 0, width)):
            if np.max(np.abs(pair[:, axis]-value)) <= max(8, dimension*.07):
                possible.append(name)
        if not possible:
            continue
        edge = pair[1]-pair[0]
        normal = np.array([-edge[1], edge[0]], np.float32)/np.linalg.norm(edge)
        samples = pair[0]+np.linspace(.08, .92, 60)[:, None]*edge
        offsets = np.linspace(3, max(6, min(24, min(height, width)*.04)), 5)
        outer = np.rint(samples[:, None, :]-offsets[None, :, None]*normal).astype(int).reshape(-1, 2)
        valid = ((outer[:, 0] >= 0) & (outer[:, 0] < width) & (outer[:, 1] >= 0) & (outer[:, 1] < height))
        outside = gray[outer[valid, 1], outer[valid, 0]]
        # A dark majority plus a dark median tolerates anti-aliasing/glare at
        # the screen's thin rim; it does not classify ordinary brown wood.
        if valid.mean() < .45 or np.mean(outside < 80) < .75 or np.median(outside) >= 65:
            continue
        found[index] = possible[0]
    return found


def _paper_color_centers(gray, lab):
    """Dominant bright-region Lab modes; no central-ROI or green-UI assumption.

    Four-level bins plus a 3x3 sum make quantization stable. Return at most three
    distinct median colors. Candidates still need geometry, text and outside
    transitions: a dominant color alone never establishes a document.
    """
    ab = lab[:, :, 1:]
    eligible = ((gray >= 120) & (gray <= 245) & (np.linalg.norm(ab-128, axis=2) <= 48))
    values = ab[eligible]
    if len(values) < 64:
        return []
    bins = (values//4).astype(np.int32)
    histogram = np.bincount(bins[:, 0]*64+bins[:, 1], minlength=4096).reshape(64, 64).astype(np.float32)
    mass = cv2.boxFilter(histogram, -1, (3, 3), normalize=False, borderType=cv2.BORDER_CONSTANT)
    centers = []
    for flat in np.argsort(mass.ravel())[::-1]:
        if mass.ravel()[flat] < max(64, len(values)*.02):
            break
        coarse = np.array([flat//64*4+2, flat % 64*4+2], np.float32)
        if any(np.linalg.norm(coarse-center) < 8 for center in centers):
            continue
        local = values[np.max(np.abs(values-coarse), axis=1) <= 6]
        if len(local) < 64:
            continue
        center = np.median(local, axis=0).astype(np.float32)
        if any(np.linalg.norm(center-old) < 8 for old in centers):
            continue
        centers.append(center)
        if len(centers) == 3:
            break
    return centers


def _score_clipped_region(gray, chroma, mask, distance, all_edges, quad, agreement, chroma_limit, cut_sides=None):
    """Require observed paper/background transitions on every non-frame side.

    Frame cuts are deliberately exempt from edge evidence, but at least two
    real sides must remain. This does not relax the complete-paper scorer.
    """
    height, width = gray.shape
    frame_sides = _frame_sides(quad, gray.shape) if cut_sides is None else cut_sides
    if not 1 <= len(frame_sides) <= 2:
        return None
    ratio = cv2.contourArea(quad)/(height*width)
    sides = np.roll(quad, -1, axis=0)-quad
    lengths = np.linalg.norm(sides, axis=1)
    if not .035 <= ratio <= .90 or max(lengths)/min(lengths) > 18:
        return None
    inset = max(5., min(14., float(min(lengths))*.035))
    tolerance = max(3., float(min(lengths))*.025)
    supports, color_steps = [], []
    for index, edge in enumerate(sides):
        if index in frame_sides:
            continue
        samples = quad[index]+np.linspace(.04, .96, 100)[:, None]*edge
        normal = np.array([-edge[1], edge[0]], np.float32)/lengths[index]
        inner = np.rint(samples+normal*inset).astype(int)
        outer = np.rint(samples-normal*inset).astype(int)
        valid = ((inner[:, 0] >= 0) & (inner[:, 0] < width) & (inner[:, 1] >= 0) & (inner[:, 1] < height)
                 & (outer[:, 0] >= 0) & (outer[:, 0] < width) & (outer[:, 1] >= 0) & (outer[:, 1] < height))
        if valid.sum() < 75:
            return None
        inside_gray, outside_gray = gray[inner[valid, 1], inner[valid, 0]], gray[outer[valid, 1], outer[valid, 0]]
        inside_chroma = chroma[inner[valid, 1], inner[valid, 0]]
        outside_chroma = chroma[outer[valid, 1], outer[valid, 0]]
        if np.median(inside_gray) < 125 or np.median(inside_chroma) > chroma_limit+2:
            return None
        transitions = ((outside_chroma-inside_chroma >= 6)
                       | (inside_gray.astype(float)-outside_gray.astype(float) >= 15))
        color_steps.append(float(np.mean(transitions)))
        sampled = np.rint(samples).astype(int)
        supports.append(float(np.mean(distance[sampled[:, 1], sampled[:, 0]] <= tolerance)))
    if min(color_steps) < .65 or min(supports) < .55:
        return None
    polygon = np.zeros_like(gray)
    cv2.fillConvexPoly(polygon, np.rint(quad).astype(np.int32), 255)
    inside = cv2.erode(polygon, np.ones((7, 7), np.uint8)) > 0
    values = gray[inside]
    fill = float(np.mean(mask[inside] > 0))
    bright = float(np.mean(values >= 130))
    density = float(np.mean(all_edges[inside] > 0))
    # A blank panel or glare stripe alone does not establish a clipped receipt.
    if fill < .88 or bright < .75 or np.median(values) < 145 or not .002 <= density <= .20:
        return None
    score = (.25*min(color_steps)+.25*fill+.20*agreement+.15*np.mean(supports)+.15*bright)
    return {"score": float(min(score, .88)), "area_ratio": float(ratio),
            "observed_boundary_support": float(np.mean(supports)),
            "observed_color_transition": float(min(color_steps)), "paper_mask_fill": fill,
            "interior_edge_density": density, "chroma_limit": int(chroma_limit)}


def _visible_region_envelope(quad, mask, cut_sides=None):
    """Keep the connected paper union rather than trimming a stepped lower sheet.

    Move only observed sides outward to their component envelope. Frame sides
    remain exactly on the image; no off-image corner/content is reconstructed.
    Large departures from a four-sided region are rejected rather than boxed.
    """
    polygon = np.zeros_like(mask)
    cv2.fillConvexPoly(polygon, np.rint(quad).astype(np.int32), 255)
    _, labels = cv2.connectedComponents(mask)
    counts = np.bincount(labels[polygon > 0])
    counts[0] = 0
    label = int(np.argmax(counts))
    if counts[label] < 64:
        return None
    yy, xx = np.nonzero(labels == label)
    points = np.column_stack((xx, yy)).astype(np.float32)
    edges = np.roll(quad, -1, axis=0)-quad
    lengths = np.linalg.norm(edges, axis=1)
    normals = np.column_stack((-edges[:, 1], edges[:, 0]))/lengths[:, None]
    bounds = np.sum(normals*quad, axis=1)
    expansion = 0.
    frame_sides = _frame_sides(quad, mask.shape) if cut_sides is None else cut_sides
    for index in range(4):
        if index in frame_sides:
            continue
        observed = float(np.min(points@normals[index]))
        delta = max(0., float(bounds[index])-observed)
        if delta > max(4., float(min(lengths))*.05):
            return None
        bounds[index] -= delta
        expansion = max(expansion, delta)
    expanded = np.empty((4, 2), np.float32)
    for index in range(4):
        previous = (index-1) % 4
        try:
            expanded[index] = np.linalg.solve(normals[[previous, index]], bounds[[previous, index]])
        except np.linalg.LinAlgError:
            return None
    expanded[:, 0] = np.clip(expanded[:, 0], 0, mask.shape[1]-1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, mask.shape[0]-1)
    try:
        validate_quad(expanded, mask.shape)
    except ValueError:
        return None
    cv2.fillConvexPoly(polygon, np.rint(expanded).astype(np.int32), 255)
    coverage = float(np.mean(polygon[yy, xx] > 0))
    if coverage < .99:
        return None
    return expanded, expansion, coverage


def _clipped_paper_candidate(img, gray, distance, all_edges):
    if img.ndim != 3:
        return None
    lab = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2LAB).astype(np.float32)
    chroma = np.linalg.norm(lab[:, :, 1:]-128, axis=2)
    best = None
    for limit in (6, 10, 16):
        mask = paper_region_mask(img, limit)
        for quad, agreement in _candidates(mask, gray.shape):
            metrics = _score_clipped_region(gray, chroma, mask, distance, all_edges, quad, agreement, limit)
            if metrics is None:
                continue
            envelope = _visible_region_envelope(quad, mask)
            if envelope is None:
                continue
            expanded, expansion, coverage = envelope
            metrics.update(envelope_max_expansion=float(expansion), paper_mask_coverage=coverage)
            if best is None or metrics["score"] > best["metrics"]["score"]:
                best = {"quad": expanded, "method": "clipped_paper_region", "metrics": metrics}
    return best


def _adaptive_paper_candidate(img, gray, distance, all_edges):
    """Extra fallback for tinted paper and a recaptured display's dark bezel."""
    if img.ndim != 3:
        return None
    lab = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2LAB).astype(np.float32)
    best = None
    for center in _paper_color_centers(gray, lab):
        relative = np.linalg.norm(lab[:, :, 1:]-center, axis=2)
        for limit in (6, 10):
            mask = paper_region_mask(img, limit, center)
            for quad, agreement in _candidates(mask, gray.shape):
                physical = _frame_sides(quad, gray.shape)
                content = _content_cut_sides(gray, quad)
                cuts = {**physical, **content}
                metrics = _score_clipped_region(gray, relative, mask, distance, all_edges, quad, agreement, limit, cuts)
                if metrics is None:
                    continue
                envelope = _visible_region_envelope(quad, mask, cuts)
                if envelope is None:
                    continue
                expanded, expansion, coverage = envelope
                metrics.update(score=min(metrics["score"], .86), paper_color_ab=center.tolist(),
                               color_distance_limit=limit, color_basis="dominant_bright_region_lab",
                               envelope_max_expansion=float(expansion), paper_mask_coverage=coverage)
                if best is None or metrics["score"] > best["metrics"]["score"]:
                    best = {"quad": expanded, "method": "adaptive_paper_region", "metrics": metrics,
                            "clipped_edges": list(cuts.values()), "content_cut_edges": list(content.values())}
    return best


def _guard_small_automatic_candidate(result, image_shape):
    """Keep the whole page when an automatic proposal is only a small panel.

    This policy is deliberately separate from manual-quad validation: users
    may explicitly select a small document, but automatic cropping must not
    silently discard most of a page around an internal title or table.
    """
    quad = result.get("quad")
    if quad is None:
        return result
    height, width = image_shape[:2]
    ratio = abs(cv2.contourArea(np.asarray(quad, np.float32))) / (height * width)
    if ratio >= .10:
        return result
    return {
        "quad": None, "confidence": 0.0, "method": "none",
        "confidence_kind": "heuristic_not_probability",
        "rejection_reason": "candidate_too_small",
        "candidate_area_ratio": float(ratio), "minimum_area_ratio": .10,
        "warnings": ["自动候选区域小于整图的10%，已保留整图；请手动调整四角。"],
        "diagnostics": {**result.get("diagnostics", {}),
                        "candidate_method": result["method"],
                        "candidate_confidence": result["confidence"],
                        "candidate_quad": np.asarray(quad).tolist()},
    }


def detect_document(img):
    """Observed paper quad with uncalibrated heuristic confidence.

    Multi-edge and bright-region proposals need continuous side support and
    three outside/inside transitions. No minAreaRect fallback invents edges.
    If complete-paper proposals fail, a neutral-color region may provide the
    visible extent of a frame-clipped sheet. Only observed sides are tested;
    cuts at the frame are labelled, never extrapolated outside the image.
    A final relative-color proposal handles moderate lighting casts and dark
    screen rims without changing the complete-paper scorer. Dark/saturated
    paper, weak boundaries and non-document panels remain limits.
    Automatic candidates under 10% of the original image keep the whole page;
    explicit manual points are handled separately and bypass this policy.
    """
    missing = {"quad": None, "confidence": 0.0, "method": "none", "confidence_kind": "heuristic_not_probability"}
    gray = _gray_image(img)
    if gray is None:
        return missing
    original_height, original_width = gray.shape
    scale = min(1.0, 960.0/max(gray.shape))
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = [cv2.Canny(blurred, low, high) for low, high in ((10, 35), (30, 90), (60, 180))]
    all_edges = np.maximum.reduce(edges)
    distance = cv2.distanceTransform(255-all_edges, cv2.DIST_L2, 3)
    kernel = np.ones((3, 3), np.uint8)
    maps = [(cv2.morphologyEx(edge, cv2.MORPH_CLOSE, kernel), "multi_edge") for edge in edges]
    otsu, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    thresholds = sorted({int(otsu), *[int(np.percentile(blurred, q)) for q in (60, 75, 88, 94)]})
    for threshold in thresholds:
        if not 80 <= threshold <= 250:
            continue
        mask = cv2.threshold(blurred, threshold, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        maps.append((cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel), "paper_region"))
    best, seen, count = None, set(), 0
    for mask, method in maps:
        for quad, agreement in _candidates(mask, gray.shape):
            key = tuple(np.rint(quad/3).astype(int).ravel())
            if key in seen:
                continue
            seen.add(key)
            metrics = _score_candidate(gray, distance, all_edges, quad, agreement)
            if metrics is None or metrics["score"] < .60:
                continue
            count += 1
            if best is None or metrics["score"] > best["metrics"]["score"]:
                best = {"quad": quad, "method": method, "metrics": metrics}
    if best is None:
        working = img if scale == 1 else cv2.resize(img, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)
        best = _clipped_paper_candidate(working, gray, distance, all_edges)
        if best is None:
            best = _adaptive_paper_candidate(working, gray, distance, all_edges)
        if best is not None:
            count = 1
    if best is None:
        return missing
    quad = best["quad"]
    if np.all(quad > 6) and np.all(quad[:, 0] < gray.shape[1]-7) and np.all(quad[:, 1] < gray.shape[0]-7):
        refined = cv2.cornerSubPix(blurred, quad.copy(), (4, 4), (-1, -1),
                                   (cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER, 30, .05))
        if float(np.max(np.linalg.norm(refined-quad, axis=1))) < 5:
            try:
                quad = validate_quad(refined, gray.shape)
            except ValueError:
                pass
    if best["method"] in ("clipped_paper_region", "adaptive_paper_region"):
        # Endpoint scaling preserves the actual cut at h-1/w-1 after resize.
        quad = quad*np.array([(original_width-1)/(gray.shape[1]-1),
                              (original_height-1)/(gray.shape[0]-1)], np.float32)
    else:
        quad = quad/scale
    quad[:, 0] = np.clip(quad[:, 0], 0, original_width-1)
    quad[:, 1] = np.clip(quad[:, 1], 0, original_height-1)
    quad = validate_quad(quad, (original_height, original_width))
    result = {"quad": quad, "confidence": round(best["metrics"]["score"], 4), "method": best["method"],
              "confidence_kind": "heuristic_not_probability", "diagnostics": {**best["metrics"], "accepted_candidates": count}}
    if best["method"] in ("clipped_paper_region", "adaptive_paper_region"):
        result.update(document_complete=False, clipped_edges=best.get("clipped_edges", list(_frame_sides(quad, (original_height, original_width)).values())),
                      overlap_possible=True, overlap_handling="visible_paper_union",
                      warnings=["纸张有部分超出画面，仅保留可见纸面，无法恢复画外内容。",
                                "范围按相连纸面整体估计，可能包含重叠纸张，请检查四角。"])
        if best["method"] == "adaptive_paper_region":
            result["content_cut_edges"] = best["content_cut_edges"]
            result["warnings"][0] = "纸面在照片或屏幕边界处可能被截断，仅保留可见部分，无法恢复画外内容。"
    return _guard_small_automatic_candidate(result, (original_height, original_width))


def find_doc_quad(img):
    return detect_document(img)["quad"]


def four_point_transform(img, quad):
    """Warp arbitrary convex points; manually connected points need validation first."""
    if not isinstance(img, np.ndarray) or img.ndim < 2 or min(img.shape[:2]) < 2:
        raise ValueError("图像尺寸无效")
    rect = validate_quad(order_points(quad), img.shape)
    tl, tr, br, bl = rect
    width = int(round(max(np.linalg.norm(br-bl), np.linalg.norm(tr-tl))))+1
    height = int(round(max(np.linalg.norm(tr-br), np.linalg.norm(tl-bl))))+1
    destination = np.array([[0, 0], [width-1, 0], [width-1,height-1], [0,height-1]], np.float32)
    transform = cv2.getPerspectiveTransform(rect, destination)
    return cv2.warpPerspective(img, transform, (width,height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def correct_document(img):
    quad = find_doc_quad(img)
    if quad is None:
        return None, img
    return quad, four_point_transform(img, quad)
