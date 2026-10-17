package com.snapscan.offline;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.json.*;

/** Shared cross-language fixture tests; synthetic coordinates never claim to be
 * recovered from the user's coordinate-free exported PDF. */
public final class TextLayoutContractTest {
    private static int checks;
    private static void require(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception {
        JSONObject fixtures=new JSONObject(new String(Files.readAllBytes(Path.of(args[0],"evaluation/fixtures/text_layout_cases.json")),StandardCharsets.UTF_8));
        JSONArray cases=fixtures.getJSONArray("cases");
        for(int i=0;i<cases.length();i++){
            JSONObject sample=cases.getJSONObject(i);String name=sample.getString("name");
            JSONArray boxes=sample.getJSONArray("boxes");String before=boxes.toString();
            JSONObject actual=TextLayout.build(boxes);
            require(actual.getString("text").equals(sample.getString("expected_text")),name+" text: "+actual);
            require(actual.getJSONArray("rows").toString().equals(sample.getJSONArray("expected_rows").toString()),name+" rows: "+actual);
            require(actual.getString("mode").equals(sample.getString("expected_mode")),name+" mode: "+actual);
            require(boxes.toString().equals(before),name+" original boxes/coordinates/text changed");
        }
        JSONArray ordinary=new JSONArray().put(new JSONObject().put("text","A").put("box",new JSONArray("[[0,0],[20,0],[20,10],[0,10]]")));
        // JSON itself rejects NaN/Infinity; malformed numeric text is tested in the shared set.
        JSONObject legacy=new JSONObject().put("text","旧记录自由文本").put("boxes",new JSONArray());
        require(TextLayout.apply(legacy).getString("text").equals("旧记录自由文本"),"empty legacy records retain text");
        require(TextLayout.build(ordinary).getString("mode").equals("spatial"),"ordinary valid geometry accepted");
        Path source=Path.of(args[0],"output/scan/android-device/receipt-document.json");
        JSONObject receipt=new JSONObject(new String(Files.readAllBytes(source),StandardCharsets.UTF_8));
        JSONArray originalBoxes=receipt.getJSONArray("boxes");String before=originalBoxes.toString();
        JSONObject real=TextLayout.build(originalBoxes);
        require(real.getString("mode").equals("spatial"),"actual prior Android receipt geometry must be valid");
        require(real.getString("text").contains("商品  单价  数量  价格（￥）"),"actual header row left-to-right: "+real.getString("text"));
        require(real.getString("text").contains("1.养生海参盅  58.00  6/份  348.00"),"actual dish row left-to-right");
        boolean staffTogether=false;JSONArray realRows=real.getJSONArray("rows");
        for(int i=0;i<realRows.length();i++){JSONArray row=realRows.getJSONArray(i);boolean service=false,operator=false;for(int j=0;j<row.length();j++){service|=row.getInt(j)==87;operator|=row.getInt(j)==88;}staffTogether|=service&&operator;}
        require(staffTogether,"tiny side note must not split actual same-row staff fields");
        require(before.equals(originalBoxes.toString()),"actual receipt box identity retained");
        require(TextLayout.order(real).length==originalBoxes.length(),"actual receipt preserves every box index");
        real.put("source",source.toString()).put("source_kind","prior_actual_android_ocr_boxes_not_user_pdf_coordinates")
            .put("source_box_count",originalBoxes.length()).put("checks",checks);
        Files.write(Path.of(args[0],"reports/android_text_layout_host.json"),real.toString(2).getBytes(StandardCharsets.UTF_8));
        System.out.println("PASS "+checks+" text-layout contract checks ("+cases.length()+" shared cases)");
    }
}
