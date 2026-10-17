package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import android.content.ContextWrapper;
import android.database.DatabaseErrorHandler;
import android.database.sqlite.SQLiteDatabase;
import androidx.test.platform.app.InstrumentationRegistry;
import com.tom_roush.pdfbox.pdmodel.PDDocument;
import com.tom_roush.pdfbox.text.PDFTextStripper;
import org.json.*;
import org.junit.Test;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Pattern;

/** Reads real prior Android OCR boxes without running the model. Test-only
 * geometry fixtures are synthetic; no coordinates are invented for user PDF.
 * SQLite is isolated from the app's real local history, including on failure.
 */
public class ReadingOrderAndroidTest {
    private static Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private static byte[] asset(String name)throws Exception{
        try(InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("layout/"+name);ByteArrayOutputStream output=new ByteArrayOutputStream()){
            byte[] bytes=new byte[8192];int n;while((n=input.read(bytes))!=-1)output.write(bytes,0,n);return output.toByteArray();
        }
    }
    private static JSONObject object(String name)throws Exception{return new JSONObject(new String(asset(name),StandardCharsets.UTF_8));}
    private static JSONObject artifact(File file)throws Exception{
        byte[] hash=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));StringBuilder sha=new StringBuilder();for(byte b:hash)sha.append(String.format(Locale.US,"%02x",b&255));
        return new JSONObject().put("path",file.getAbsolutePath()).put("sha256",sha).put("bytes",file.length());
    }
    @Test public void sharedRowsMatchAndKeepEveryCorrectionIndex()throws Exception {
        JSONArray cases=object("text_layout_cases.json").getJSONArray("cases");
        for(int i=0;i<cases.length();i++){
            JSONObject sample=cases.getJSONObject(i);JSONArray boxes=sample.getJSONArray("boxes");String before=boxes.toString();JSONObject actual=TextLayout.build(boxes);
            assertEquals(sample.getString("name"),sample.getString("expected_text"),actual.getString("text"));
            assertEquals(sample.getString("name"),sample.getJSONArray("expected_rows").toString(),actual.getJSONArray("rows").toString());
            assertEquals(sample.getString("name"),sample.getString("expected_mode"),actual.getString("mode"));assertEquals(before,boxes.toString());
        }
    }
    @Test public void oldHistoryReadAndBothPdfModesReconstructRowsWithoutMutatingStoredBoxes()throws Exception {
        Context context=context();File isolated=new File(context.getCacheDir(),"reading-order-isolated-"+System.nanoTime());assertTrue(isolated.mkdir());
        Context separate=new ContextWrapper(context){
            @Override public File getDatabasePath(String name){return new File(isolated,name);}
            @Override public SQLiteDatabase openOrCreateDatabase(String name,int mode,SQLiteDatabase.CursorFactory factory){return SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name),factory);}
            @Override public SQLiteDatabase openOrCreateDatabase(String name,int mode,SQLiteDatabase.CursorFactory factory,DatabaseErrorHandler handler){return SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name).getPath(),factory,handler);}
        };
        File image=new File(context.getCacheDir(),"reading-order-proof-source.png");Files.write(image.toPath(),asset("receipt-ocr-v201.png"));
        JSONObject original=object("receipt-document-v201.json");JSONArray boxes=original.getJSONArray("boxes");String boxIdentity=boxes.toString();
        JSONObject model=original.getJSONObject("model"),saved;long id;
        try(DocumentStore store=new DocumentStore(separate)){
            JSONObject inserted=store.insert(new JSONObject().put("text",original.getString("text")).put("boxes",boxes).put("model",model),"/uploads/test-only.png",original.getInt("image_width"),original.getInt("image_height"),"prior-android-evidence.png",0);
            id=inserted.getLong("id");SQLiteDatabase db=store.getWritableDatabase();
            // A legacy row has box-per-line text and revision7. A read may derive
            // better display text but must never rewrite SQL values or revision.
            String legacy=original.getString("text");db.execSQL("UPDATE documents SET text=?,revision=7 WHERE id=?",new Object[]{legacy,id});
            JSONObject read=store.get(id);String reconstructed=read.getString("text");
            assertEquals(7,read.getInt("revision"));assertEquals(boxIdentity,read.getJSONArray("boxes").toString());
            assertEquals(boxIdentity,read.getJSONArray("original_boxes").toString());
            assertTrue(reconstructed.contains("商品  单价  数量  价格（￥）"));assertTrue(reconstructed.contains("1.养生海参盅  58.00  6/份  348.00"));
            assertTrue("tiny side note must not split the two body fields",reconstructed.contains("服务员：王晓慧  操作员：王晓慧"));
            try(android.database.Cursor cursor=db.rawQuery("SELECT text,boxes,revision,image FROM documents WHERE id=?",new String[]{String.valueOf(id)})){
                assertTrue(cursor.moveToFirst());assertEquals(legacy,cursor.getString(0));assertEquals(boxIdentity,cursor.getString(1));assertEquals(7,cursor.getInt(2));assertEquals("/uploads/test-only.png",cursor.getString(3));
            }
            JSONArray edited=new JSONArray();for(int i=0;i<boxes.length();i++)edited.put(boxes.getJSONObject(i).getString("text"));edited.put(0,"布局校对样例");
            saved=store.save(id,edited,7);assertEquals(8,saved.getInt("revision"));assertEquals("布局校对样例",saved.getJSONArray("boxes").getJSONObject(0).getString("text"));
            assertEquals(boxIdentity,saved.getJSONArray("original_boxes").toString());assertEquals(TextLayout.build(saved.getJSONArray("boxes")).getString("text"),saved.getString("text"));
            for(int i=0;i<boxes.length();i++)assertEquals(boxes.getJSONObject(i).getJSONArray("box").toString(),saved.getJSONArray("boxes").getJSONObject(i).getJSONArray("box").toString());
            try{store.save(id,edited,7);fail("stale revision accepted");}catch(NativeApi.ApiException expected){assertEquals(409,expected.statusCode);}
        }finally{SQLiteDatabase.deleteDatabase(new File(isolated,"snapscan-local.db"));isolated.delete();}
        JSONObject outputs=new JSONObject().put("source_image",artifact(image));
        for(String mode:new String[]{"text","searchable"}){
            File pdf=new File(context.getCacheDir(),"reading-order-proof-"+mode+".pdf");DocumentExporter.pdf(context,saved,image,pdf,mode);
            try(PDDocument document=PDDocument.load(pdf)){
                String extracted=new PDFTextStripper().getText(document);
                assertTrue(mode+" header order: "+extracted,Pattern.compile("商品\\s*单价\\s*数量\\s*价格（￥）").matcher(extracted).find());
                assertTrue(mode+" dish order",Pattern.compile("1\\.养生海参盅\\s*58\\.00\\s*6/份\\s*348\\.00").matcher(extracted).find());
                assertTrue(extracted.contains("布局校对样例"));
                assertEquals("receipt should no longer expand each fragment into its own page line",1,document.getNumberOfPages());
                if(mode.equals("searchable"))assertTrue(document.getPage(0).getResources().getXObjectNames().iterator().hasNext());
            }
            outputs.put(mode+"_pdf",artifact(pdf));
        }
        File excel=new File(context.getCacheDir(),"reading-order-proof-ocr-details.xlsx");DocumentExporter.excel(saved.getJSONArray("boxes"),excel);outputs.put("excel",artifact(excel));
        File layout=new File(context.getCacheDir(),"reading-order-proof-layout.json");
        Files.write(layout.toPath(),saved.toString(2).getBytes(StandardCharsets.UTF_8));outputs.put("layout_json",artifact(layout));
        File text=new File(context.getCacheDir(),"reading-order-proof-text.txt");Files.write(text.toPath(),saved.getString("text").getBytes(StandardCharsets.UTF_8));outputs.put("text",artifact(text));
        File rows=new File(context.getCacheDir(),"reading-order-proof-rows.json");Files.write(rows.toPath(),saved.getJSONArray("text_rows").toString(2).getBytes(StandardCharsets.UTF_8));outputs.put("rows_json",artifact(rows));
        JSONObject proof=new JSONObject().put("schema",1).put("version_code",BuildConfig.VERSION_CODE).put("version_name",BuildConfig.VERSION_NAME)
            .put("runtime","actual_android_document_store_and_pdfbox").put("source_kind","prior_actual_android_ocr_boxes_not_user_pdf_coordinates")
            .put("document_id",id).put("revision",8).put("database_scope","isolated_cache_database_deleted_after_test")
            .put("legacy_read_wrote_db",false).put("original_box_count",boxes.length()).put("text_rows",saved.getJSONArray("text_rows"))
            .put("artifacts",outputs);
        Files.write(new File(context.getCacheDir(),"reading-order-proof.json").toPath(),proof.toString(2).getBytes(StandardCharsets.UTF_8));
    }
}
