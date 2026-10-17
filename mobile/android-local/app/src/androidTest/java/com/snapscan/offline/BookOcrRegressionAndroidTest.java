package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.*;
import org.junit.Test;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;
import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Old DEV fixes only. New holdout images/results never tune these assertions. */
public class BookOcrRegressionAndroidTest {
    private static Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private static byte[] read(InputStream input)throws Exception{try(InputStream in=input;ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1)out.write(b,0,n);return out.toByteArray();}}
    private static InputStream asset(String name)throws Exception{return InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("book_ocr/"+name);}
    private static String sha(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format(Locale.US,"%02x",b&255));return out.toString();}
    private static JSONObject artifact(File file)throws Exception{return new JSONObject().put("path",file.getAbsolutePath()).put("sha256",sha(Files.readAllBytes(file.toPath()))).put("bytes",file.length());}
    private static float[][] values(Mat image){float[] flat=new float[(int)image.total()];image.get(0,0,flat);float[][] out=new float[image.rows()][image.cols()];for(int y=0;y<image.rows();y++)System.arraycopy(flat,y*image.cols(),out[y],0,image.cols());return out;}
    private static String normalized(String text){StringBuilder out=new StringBuilder();for(String line:text.split("\\r\\n|\\r|\\n",-1))if(!line.contains("[UNK]"))out.append(line);return out.toString().replace(" ","").replace("\t","").replace("　","");}
    private static int distance(String a,String b){int[] old=new int[b.length()+1];for(int j=0;j<old.length;j++)old[j]=j;for(int i=1;i<=a.length();i++){int[] next=new int[b.length()+1];next[0]=i;for(int j=1;j<=b.length();j++)next[j]=Math.min(old[j]+1,Math.min(next[j-1]+1,old[j-1]+(a.charAt(i-1)==b.charAt(j-1)?0:1)));old=next;}return old[b.length()];}
    @Test public void cachedCurvedTextAndWeakNoiseKeepTheSameScoreThreshold()throws Exception {
        assertTrue(OpenCVLoader.initLocal());JSONObject manifest=new JSONObject(new String(read(asset("manifest.json")),StandardCharsets.UTF_8));
        byte[] raw=read(new GZIPInputStream(asset("real-010-probability.f32gzip")));assertEquals(manifest.getString("probability_raw_sha256"),sha(raw));
        int width=manifest.getInt("width"),height=manifest.getInt("height");assertEquals(width*height*4,raw.length);
        float[] flat=new float[width*height];ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(flat);
        float[][] pred=new float[height][width];for(int y=0;y<height;y++)System.arraycopy(flat,y*width,pred[y],0,width);
        assertEquals(42,OcrEngine.postprocess(pred,width,height).size());
        Mat curved=new Mat(180,800,CvType.CV_32F,new Scalar(.01));ArrayList<Point> points=new ArrayList<>();for(int x=40;x<760;x++)points.add(new Point(x,80+28*Math.sin(x/110.0)));
        MatOfPoint contour=new MatOfPoint();contour.fromList(points);Imgproc.polylines(curved,Collections.singletonList(contour),false,new Scalar(.95),6);contour.release();
        try{List<Point[]> boxes=OcrEngine.postprocess(values(curved),800,180);assertEquals(1,boxes.size());double low=Double.POSITIVE_INFINITY,high=Double.NEGATIVE_INFINITY;for(Point p:boxes.get(0)){low=Math.min(low,p.y);high=Math.max(high,p.y);}assertTrue("padding must not include multiple neighboring lines",high-low<90);}finally{curved.release();}
        Mat weak=new Mat(100,500,CvType.CV_32F,new Scalar(.01));Imgproc.rectangle(weak,new Point(20,35),new Point(480,55),new Scalar(.4),-1);
        try{assertTrue(".5 contour gate remains enforced",OcrEngine.postprocess(values(weak),500,100).isEmpty());}finally{weak.release();}
    }
    @Test public void actualOldDevBookRecoversOpeningMiddleEndingWithManualGroundTruth()throws Exception {
        assertTrue(OpenCVLoader.initLocal());Context context=context();JSONObject manifest=new JSONObject(new String(read(asset("manifest.json")),StandardCharsets.UTF_8));
        byte[] input=read(asset("real-010-old-dev.jpg"));assertEquals(manifest.getString("image_sha256"),sha(input));
        File source=new File(context.getCacheDir(),"book-ocr-proof-source.jpg");Files.write(source.toPath(),input);
        JSONObject gt=new JSONObject(new String(read(asset("real-010-gt.json")),StandardCharsets.UTF_8));String reference=normalized(gt.getString("gt_text"));assertEquals("dev",gt.getString("split"));
        Mat image=NativeApi.readBgr(source),before=image.clone();JSONObject result;
        try(OcrEngine engine=new OcrEngine(context)){
            result=engine.recognize(image);assertEquals(0,Core.norm(image,before,Core.NORM_INF),0);
            String text=normalized(result.getString("text"));int errors=distance(text,reference);double cer=errors/(double)reference.length();
            assertTrue("opening body must be present",text.contains("社会都得到提高的地位"));
            assertTrue("middle body must be present",text.contains("我们的使命绝不是求得"));
            assertTrue("ending body must be present",text.contains("精神原则和肉体原则"));
            assertTrue("manual-GT CER, not length alone: "+cer,cer<.06);
            File prediction=new File(context.getCacheDir(),"book-ocr-proof-prediction.json");Files.write(prediction.toPath(),result.toString(2).getBytes(StandardCharsets.UTF_8));
            File textFile=new File(context.getCacheDir(),"book-ocr-proof-text.txt");Files.write(textFile.toPath(),result.getString("text").getBytes(StandardCharsets.UTF_8));
            Mat overlay=image.clone();try{JSONArray boxes=result.getJSONArray("boxes");for(int i=0;i<boxes.length();i++){JSONArray box=boxes.getJSONObject(i).getJSONArray("box");Point[] q=new Point[4];for(int j=0;j<4;j++)q[j]=new Point(box.getJSONArray(j).getDouble(0),box.getJSONArray(j).getDouble(1));MatOfPoint poly=new MatOfPoint(q);try{Imgproc.polylines(overlay,Collections.singletonList(poly),true,new Scalar(0,0,255),4);}finally{poly.release();}}File overlayFile=new File(context.getCacheDir(),"book-ocr-proof-overlay.jpg");assertTrue(Imgcodecs.imwrite(overlayFile.getAbsolutePath(),overlay));
                JSONObject artifacts=new JSONObject().put("source_image",artifact(source)).put("prediction_json",artifact(prediction)).put("text",artifact(textFile)).put("overlay",artifact(overlayFile));
                JSONObject proof=new JSONObject().put("schema",1).put("version_code",BuildConfig.VERSION_CODE).put("version_name",BuildConfig.VERSION_NAME).put("source_image_id","real-010.jpg").put("source_split","dev").put("holdout_read",false).put("source_kind","old_dev_regression_not_new_holdout").put("runtime","actual_android_onnxruntime_opencv").put("model",result.getJSONObject("model")).put("normalized_gt_chars",reference.length()).put("normalized_pred_chars",text.length()).put("edit_distance",errors).put("CER",cer).put("boxes",result.getJSONArray("boxes").length()).put("artifacts",artifacts);
                Files.write(new File(context.getCacheDir(),"book-ocr-proof.json").toPath(),proof.toString(2).getBytes(StandardCharsets.UTF_8));
            }finally{overlay.release();}
        }finally{image.release();before.release();}
    }
}
