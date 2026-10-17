package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.*;
import org.junit.Test;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Locale;

/** Real NativeApi/Bitmap/JNI regression. Photo fixture retains screenshot UI;
 * other fixtures are explicitly rendered from the old photo without any UI.
 * Test inputs are androidTest assets only and never enter the release APK.
 */
public class ScreenPaperAndroidTest {
    private static final String[] CASES={"photo_with_ui","blue_mild","blue_medium","warm","black_border","shadow","blue_shadow_border","moire"};
    private static final String[] NEGATIVES={"wood_blue_mild","wood_blue_medium","wood_warm","wood_border","wood_combined","wood_moire"};
    private static String sha(File file)throws Exception{byte[] b=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));StringBuilder s=new StringBuilder();for(byte v:b)s.append(String.format(Locale.US,"%02x",v&255));return s.toString();}
    private static JSONObject file(File file)throws Exception{return new JSONObject().put("path",file.getAbsolutePath()).put("sha256",sha(file)).put("bytes",file.length());}
    private static double iou(JSONArray a,double[][] reference)throws Exception{
        Point[] p=new Point[4],q=new Point[4];for(int i=0;i<4;i++){p[i]=new Point(a.getJSONArray(i).getDouble(0),a.getJSONArray(i).getDouble(1));q[i]=new Point(reference[i][0],reference[i][1]);}
        MatOfPoint2f x=new MatOfPoint2f(p),y=new MatOfPoint2f(q);Mat intersection=new Mat();try{double s=Imgproc.intersectConvexConvex(x,y,intersection);return s/(Imgproc.contourArea(x)+Imgproc.contourArea(y)-s);}finally{x.release();y.release();intersection.release();}
    }
    @Test public void screenRecaptureCasesUseLocalPipelineAndWriteReviewProof()throws Exception{
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();JSONArray cases=new JSONArray();
        try(NativeApi api=new NativeApi(context)){
            for(String name:CASES){
                String asset="scan/android_blue_"+name+".png";File source;
                try(InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open(asset)){source=api.getFiles().importImage(input,asset);}
                String before=sha(source);
                JSONObject result=api.execute("correct",new JSONObject().put("filePath",api.getFiles().url(source)).put("mode","clean"));
                assertTrue(name+" did not detect visible paper",result.getBoolean("has_quad"));
                JSONArray quad=result.getJSONArray("quad");
                double[][] reference=name.equals("photo_with_ui")?new double[][]{{144,29},{784,10},{809,1457},{209,1457}}:new double[][]{{166,0},{553,0},{585,884},{170,884}};
                if(name.contains("border")||name.equals("moire"))for(double[] point:reference){point[0]+=18;point[1]+=24;}
                double overlap=iou(quad,reference);assertTrue(name+" selected UI or lost paper: IoU="+overlap,overlap>.90);
                assertEquals("correct must not modify imported input",before,sha(source));
                JSONObject detection=result.getJSONObject("detection");assertTrue(name+" needs explicit incomplete-document metadata",detection.has("document_complete"));assertFalse(name+" must not imply restoration of off-frame content",detection.getBoolean("document_complete"));
                if(detection.getString("method").equals("adaptive_paper_region"))assertEquals("relative color masks must not erase paper pixels",0,detection.getInt("background_pixels_whitened"));
                JSONObject row=new JSONObject().put("name",name).put("source_kind",name.equals("photo_with_ui")?"screenshot_photo_region_with_ui_overlay":"rendered_no_ui_from_old_photo")
                    .put("input_sha256",before).put("source_size",result.getJSONArray("source_size")).put("size",result.getJSONArray("size"))
                    .put("reference_iou",overlap).put("quad",quad).put("detection",detection).put("warnings",result.getJSONArray("warnings"));
                if(name.equals("photo_with_ui"))row.put("screenshot_crop_xyxy",new JSONArray(new int[]{84,454,1178,1912}));
                if(name.equals("photo_with_ui")||name.equals("blue_shadow_border")){
                    JSONObject artifacts=new JSONObject();
                    for(String key:new String[]{"original_image","cropped_image","image"}){
                        File produced=api.getFiles().resolve(result.getString(key));String extension=key.equals("image")?"png":"jpg";
                        File cached=new File(context.getCacheDir(),"screen-proof-"+name+"-"+key+"."+extension);
                        Files.copy(produced.toPath(),cached.toPath(),StandardCopyOption.REPLACE_EXISTING);artifacts.put(key,file(cached));
                    }
                    row.put("artifacts",artifacts);
                }
                cases.put(row);
            }
            for(String name:NEGATIVES){
                String asset="scan/android_blue_"+name+".png";File source;
                try(InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open(asset)){source=api.getFiles().importImage(input,asset);}
                String before=sha(source);
                JSONObject result=api.execute("correct",new JSONObject().put("filePath",api.getFiles().url(source)).put("mode","clean"));
                assertFalse(name+" manufactured a receipt from wood/glare",result.getBoolean("has_quad"));
                assertEquals("negative input must not change",before,sha(source));
                cases.put(new JSONObject().put("name",name).put("source_kind","synthetic_wood_glare_no_document")
                    .put("input_sha256",before).put("source_size",result.getJSONArray("source_size"))
                    .put("has_quad",false).put("detection",result.getJSONObject("detection")));
            }
            Mat large=new Mat(720,960,CvType.CV_8UC3,new Scalar(30,30,30));
            try{
                Imgproc.rectangle(large,new Point(35,35),new Point(925,685),new Scalar(235,235,235),-1);
                for(int y=130;y<650;y+=70)Imgproc.putText(large,"LARGE PAPER WITH TEXT",new Point(130,y),Imgproc.FONT_HERSHEY_SIMPLEX,1,new Scalar(30,30,30),2);
                try(ScanProcessor.Result result=ScanProcessor.process(large,null,"clean")){
                    assertTrue("large complete paper must not be rejected as a screen",result.hasQuad);
                    assertFalse("complete-paper path must remain active",result.detection.getString("method").equals("adaptive_paper_region"));
                    double overlap=iou(result.quad,new double[][]{{35,35},{925,35},{925,685},{35,685}});assertTrue(overlap>.97);
                    cases.put(new JSONObject().put("name","large_white_paper_black_desk").put("source_kind","synthetic_full_document_positive").put("reference_iou",overlap).put("detection",result.detection));
                }
            }finally{large.release();}
        }
        JSONObject proof=new JSONObject().put("schema",1).put("runtime","actual_android_native_api_opencv")
            .put("test_set_read",false).put("photo_is_camera_original",false).put("cases",cases);
        Files.write(new File(context.getCacheDir(),"screen-proof.json").toPath(),proof.toString(2).getBytes(StandardCharsets.UTF_8));
    }
}
