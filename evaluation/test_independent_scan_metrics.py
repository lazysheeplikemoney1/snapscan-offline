import numpy as np
from independent_scan_metrics import evaluate_regions


def box(x, y, text):
    return {"box": [[x,y],[x+80,y],[x+80,y+12],[x,y+12]], "text": text}


def test_whole_page_predictions_are_scored_only_in_declared_regions():
    regions=[{"region_id":"body", "polygon":[[0,0],[100,0],[100,70],[0,70]], "text":"首行\n末行"}]
    result={"boxes":[box(2,5,"首行"),box(200,5,"图内不计分文字")], "text_rows":[[0,1]]}
    metric=evaluate_regions(result,regions)
    assert (metric["S"],metric["D"],metric["I"],metric["N"]) == (0,2,0,4)
    assert metric["unscored_prediction_boxes"] == [1]


def test_clean_scan_boxes_are_mapped_back_before_region_assignment():
    region={"region_id":"body", "polygon":[[100,200],[200,200],[200,270],[100,270]], "text":"文字"}
    result={"boxes":[box(0,0,"文字")],"text_rows":[[0]]}
    inverse=np.array([[1,0,100],[0,1,200],[0,0,1]],dtype=np.float64)
    metric=evaluate_regions(result,[region],inverse)
    assert metric["cer"] == 0
    assert metric["regions"][0]["box_indices"] == [0]


def test_each_prediction_is_assigned_at_most_once_even_with_overlapping_regions():
    region={"polygon":[[0,0],[100,0],[100,70],[0,70]],"text":"文字"}
    result={"boxes":[box(2,5,"文字")],"text_rows":[[0]]}
    metric=evaluate_regions(result,[{**region,"region_id":"a"},{**region,"region_id":"b"}])
    assert sum(len(r["box_indices"]) for r in metric["regions"]) == 1
    assert metric["D"] == 2
