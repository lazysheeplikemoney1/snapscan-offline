package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.json.*;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.Mat;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/** Frozen public evaluation inputs live only in the test APK. No GT enters OCR.
 * Accuracy is scored externally; this test records actual Android output even
 * when it is empty or wrong, without writing the user's document database. */
public class IndependentScanAndroidTest {
    @Test public void frozenNewPagesRunThroughBothOfflinePaths() throws Exception {
        Context target = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Context tests = InstrumentationRegistry.getInstrumentation().getContext();
        assertNotEquals(android.content.pm.PackageManager.PERMISSION_GRANTED, target.checkSelfPermission("android.permission.INTERNET"));
        assertTrue(OpenCVLoader.initLocal());
        JSONObject manifest;
        try (InputStream in=tests.getAssets().open("independent-20261016/inputs.json")) {
            manifest=new JSONObject(new String(read(in),StandardCharsets.UTF_8));
        }
        JSONArray inputs=manifest.getJSONArray("samples"); assertTrue(inputs.length()>=18);
        File output=new File(target.getCacheDir(),"independent-scan-20261016"); assertTrue(output.isDirectory()||output.mkdirs());
        String runId=java.util.UUID.randomUUID().toString();
        File complete=new File(output,"complete.json");
        if(complete.exists()) assertTrue(complete.delete());
        try (OcrEngine engine=new OcrEngine(target)) {
            for(int i=0;i<inputs.length();i++) {
                JSONObject item=inputs.getJSONObject(i); String id=item.getString("image_id");
                assertTrue(id.matches("[A-Za-z0-9_.-]+"));
                byte[] bytes;
                try(InputStream in=tests.getAssets().open("independent-20261016/"+item.getString("asset"))) {bytes=read(in);}
                assertEquals(item.getString("sha256"),sha(bytes));
                File source=new File(output,id+"-source"); Files.write(source.toPath(),bytes);
                Mat image=NativeApi.readBgr(source);
                try {
                    JSONObject record=new JSONObject().put("image_id",id).put("version_code",BuildConfig.VERSION_CODE)
                        .put("run_id",runId)
                        .put("lock_sha256",manifest.getString("lock_sha256")).put("input_sha256",sha(bytes))
                        .put("width",image.cols()).put("height",image.rows()).put("raw",recognize(engine,image));
                    try(ScanProcessor.Result scan=ScanProcessor.process(image,null,"clean")) {
                        record.put("clean",recognize(engine,scan.enhanced));
                        record.put("scan",new JSONObject().put("has_quad",scan.hasQuad).put("quad",scan.quad==null?JSONObject.NULL:scan.quad)
                            .put("detection",scan.detection).put("quality",scan.quality).put("warnings",scan.warnings)
                            .put("width",scan.enhanced.cols()).put("height",scan.enhanced.rows()));
                    } catch(Exception failure) {record.put("scan_error",failure.toString());}
                    Files.write(new File(output,id+".json").toPath(),record.toString(2).getBytes(StandardCharsets.UTF_8));
                } finally {image.release(); assertTrue(source.delete());}
            }
        }
        Files.write(complete.toPath(),new JSONObject().put("images",inputs.length()).put("run_id",runId)
            .put("lock_sha256",manifest.getString("lock_sha256")).put("version_code",BuildConfig.VERSION_CODE).toString(2).getBytes(StandardCharsets.UTF_8));
    }
    static JSONObject recognize(OcrEngine engine,Mat image) throws Exception {
        long start=System.nanoTime();
        try {return new JSONObject().put("result",engine.recognize(image)).put("elapsed_ms",(System.nanoTime()-start)/1e6);}
        catch(Exception failure) {return new JSONObject().put("error",failure.toString()).put("elapsed_ms",(System.nanoTime()-start)/1e6);}
    }
    static byte[] read(InputStream input) throws IOException {ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buf=new byte[65536];int n;while((n=input.read(buf))!=-1)out.write(buf,0,n);return out.toByteArray();}
    static String sha(byte[] bytes) throws Exception {StringBuilder out=new StringBuilder();for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return out.toString();}
}
