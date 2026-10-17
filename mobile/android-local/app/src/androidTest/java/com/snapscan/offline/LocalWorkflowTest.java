package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import com.tom_roush.pdfbox.pdmodel.PDDocument;
import com.tom_roush.pdfbox.text.PDFTextStripper;

/** Executes real image/model/storage/export operations on Android, without HTTP. */
public class LocalWorkflowTest {
  @Test public void oversizedPngPersistsTheExactImageUsedForOcr() throws Exception {
    Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
    try(NativeApi api=new NativeApi(context)) {
      File source=api.getFiles().allocate("source","png");
      android.graphics.Bitmap bitmap=android.graphics.Bitmap.createBitmap(5000,128,android.graphics.Bitmap.Config.ARGB_8888);
      android.graphics.Canvas canvas=new android.graphics.Canvas(bitmap);canvas.drawColor(android.graphics.Color.WHITE);
      android.graphics.Paint ink=new android.graphics.Paint();ink.setColor(android.graphics.Color.BLACK);ink.setTextSize(64);canvas.drawText("933.00",150,90,ink);
      try(OutputStream out=new FileOutputStream(source)){assertTrue(bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG,100,out));}finally{bitmap.recycle();}
      org.opencv.core.Mat input=NativeApi.readBgr(source);
      try {
        assertEquals(2500,input.cols());
        JSONObject result=api.execute("recognize",new JSONObject().put("filePath",api.getFiles().url(source)));
        File persisted=api.getFiles().resolve(result.getString("image"));
        android.graphics.BitmapFactory.Options bounds=new android.graphics.BitmapFactory.Options();bounds.inJustDecodeBounds=true;
        android.graphics.BitmapFactory.decodeFile(persisted.getAbsolutePath(),bounds);
        assertEquals("Stored dimensions must describe the actual OCR image",input.cols(),bounds.outWidth);
        assertEquals(input.rows(),bounds.outHeight);
        org.opencv.core.Mat actual=NativeApi.readBgr(persisted);
        try{assertEquals(0,org.opencv.core.Core.norm(input,actual,org.opencv.core.Core.NORM_INF),0);}finally{actual.release();}
        JSONObject pdf=api.execute("toPDF",new JSONObject().put("scan_id",result.getLong("id")).put("revision",0).put("mode","searchable"));
        assertTrue(api.getFiles().resolve(pdf.getString("file")).length()>0);
      }finally{input.release();}
    }
  }
  @Test public void localFilesRejectRemoteAndTraversal() throws Exception {
    Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
    LocalFiles files=new LocalFiles(context);
    for(String bad:new String[]{"http://192.168.1.2/uploads/a.png","https://evil.test/uploads/a.png","/uploads/../snapscan.db","file:///sdcard/private.png","/uploads/%2e%2e%2fprivate.png"}) {
      try { files.resolve(bad); fail("accepted "+bad); } catch(IOException expected) { }
    }
  }
  @Test public void realReceiptOfflineCropOcrCorrectionHistoryAndExport() throws Exception {
    Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
    assertTrue("INTERNET permission must be absent", context.checkSelfPermission("android.permission.INTERNET")!=android.content.pm.PackageManager.PERMISSION_GRANTED);
    JSONObject saved; long id; JSONObject proof=new JSONObject();
    try(NativeApi api=new NativeApi(context)) {
      assertTrue(api.execute("health",new JSONObject()).getJSONObject("ocr").getBoolean("ready"));
      int before=api.execute("history",new JSONObject()).getJSONArray("items").length();
      File source;
      try(InputStream in=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("receipt-feedback.png")) {source=api.getFiles().importImage(in,"receipt.png");}
      JSONObject scan=api.execute("correct",new JSONObject().put("filePath",api.getFiles().url(source)).put("mode","clean"));
      assertTrue(scan.getBoolean("has_quad"));
      assertEquals("clipped_paper_region",scan.getJSONObject("detection").getString("method"));
      assertFalse(scan.getJSONObject("detection").getBoolean("document_complete"));
      assertTrue(scan.getJSONArray("size").getInt(1)<500);
      assertEquals(before,api.execute("history",new JSONObject()).getJSONArray("items").length());
      JSONObject ocr=api.execute("recognize",new JSONObject().put("filePath",scan.getString("image")));
      assertTrue(ocr.getJSONArray("boxes").length()>15); assertTrue(ocr.getString("text").contains("933"));
      assertEquals("rec_head_v1",ocr.getJSONObject("model").getString("id"));
      id=ocr.getLong("id");
      assertTrue(java.util.Arrays.equals(java.nio.file.Files.readAllBytes(api.getFiles().resolve(scan.getString("image")).toPath()),java.nio.file.Files.readAllBytes(api.getFiles().resolve(ocr.getString("image")).toPath())));
      JSONObject detail=api.execute("detail",new JSONObject().put("id",id)).getJSONObject("document");
      JSONArray texts=new JSONArray(); for(int i=0;i<detail.getJSONArray("boxes").length();i++) texts.put(detail.getJSONArray("boxes").getJSONObject(i).getString("text"));
      texts.put(0,"离线校对测试 =1+1");
      saved=api.execute("saveDocument",new JSONObject().put("id",id).put("texts",texts).put("revision",0)).getJSONObject("document");
      assertEquals(1,saved.getInt("revision"));
      try {api.execute("saveDocument",new JSONObject().put("id",id).put("texts",texts).put("revision",0));fail("stale revision accepted");}catch(NativeApi.ApiException expected){assertEquals(409,expected.statusCode);}
      assertTrue(api.execute("history",new JSONObject().put("q","离线校对测试")).getJSONArray("items").length()>0);
      JSONObject pdf=api.execute("toPDF",new JSONObject().put("scan_id",id).put("revision",1).put("mode","searchable"));
      try(PDDocument document=PDDocument.load(api.getFiles().resolve(pdf.getString("file")))) {
        assertEquals(1,document.getNumberOfPages()); assertTrue(new PDFTextStripper().getText(document).contains("离线校对测试"));
        assertTrue(document.getPage(0).getResources().getXObjectNames().iterator().hasNext());
      }
      JSONObject excel=api.execute("toExcel",new JSONObject().put("scan_id",id).put("revision",1));
      try(java.util.zip.ZipFile zip=new java.util.zip.ZipFile(api.getFiles().resolve(excel.getString("file")))) {
        String xml=new String(read(zip.getInputStream(zip.getEntry("xl/worksheets/sheet1.xml"))),StandardCharsets.UTF_8);
        assertTrue(xml.contains("离线校对测试 =1+1")); assertFalse(xml.contains("<f>"));
      }
      JSONObject artifacts=new JSONObject();
      for(String[] entry:new String[][]{{"corrected",scan.getString("image")},{"ocr",ocr.getString("image")},{"pdf",pdf.getString("file")},{"excel",excel.getString("file")}})
        artifacts.put(entry[0],new JSONObject().put("path",entry[1]).put("sha256",sha256(api.getFiles().resolve(entry[1]))));
      proof.put("document",saved).put("artifacts",artifacts).put("version_code",BuildConfig.VERSION_CODE).put("version_name",BuildConfig.VERSION_NAME);
    }
    try(NativeApi reopened=new NativeApi(context)) {assertEquals(saved.toString(),reopened.execute("detail",new JSONObject().put("id",id)).getJSONObject("document").toString());}
    // API reading-order text may be derived; preserve the unchanged raw database entity separately.
    proof.put("stored_document",rawDocument(context,id));
    // Only publish artifact references after the entire real workflow and database reopen passed.
    try(Writer output=new OutputStreamWriter(new FileOutputStream(new File(context.getCacheDir(),"workflow-proof.json")),StandardCharsets.UTF_8)){output.write(proof.toString(2));}
  }
  private static JSONObject rawDocument(Context context,long id)throws Exception {
    try(DocumentStore helper=new DocumentStore(context);android.database.Cursor cursor=helper.getReadableDatabase().rawQuery("SELECT * FROM documents WHERE id=?",new String[]{String.valueOf(id)})){
      assertTrue(cursor.moveToFirst());JSONObject raw=new JSONObject();
      for(int i=0;i<cursor.getColumnCount();i++){
        String name=cursor.getColumnName(i);
        if(name.equals("boxes")||name.equals("original_boxes"))raw.put(name,new JSONArray(cursor.getString(i)));
        else if(name.equals("model"))raw.put(name,new JSONObject(cursor.getString(i)));
        else if(cursor.getType(i)==android.database.Cursor.FIELD_TYPE_INTEGER)raw.put(name,cursor.getLong(i));
        else raw.put(name,cursor.getString(i));
      }return raw;
    }
  }
  private static String sha256(File file)throws Exception {java.security.MessageDigest digest=java.security.MessageDigest.getInstance("SHA-256");try(InputStream input=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=input.read(b))!=-1)digest.update(b,0,n);}StringBuilder hex=new StringBuilder();for(byte b:digest.digest())hex.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return hex.toString();}
  private static byte[] read(InputStream in)throws IOException {ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int n;while((n=in.read(buffer))>=0)out.write(buffer,0,n);return out.toByteArray();}
}
