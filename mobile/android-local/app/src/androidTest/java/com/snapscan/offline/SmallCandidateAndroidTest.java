package com.snapscan.offline;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.json.*;
import org.junit.BeforeClass;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import static org.junit.Assert.*;

/** Real JNI regression using synthetic page interiors, not holdout OCR tuning. */
@RunWith(AndroidJUnit4.class)
public final class SmallCandidateAndroidTest {
    private static final String WARNING="自动候选区域小于整图的10%，已保留整图；请手动调整四角。";
    @BeforeClass public static void openCv(){assertTrue(OpenCVLoader.initLocal());}
    private static Mat page(boolean title){
        Mat image=new Mat(720,960,CvType.CV_8UC3,new Scalar(150,150,150));
        Imgproc.putText(image,"COMPLETE PAGE",new Point(45,80),Imgproc.FONT_HERSHEY_SIMPLEX,.75,new Scalar(30,30,30),1);
        for(int y=170;y<650;y+=70)Imgproc.putText(image,"BODY TEXT",new Point(45,y),Imgproc.FONT_HERSHEY_SIMPLEX,.5,new Scalar(35,35,35),1);
        Imgproc.putText(image,"FOOTER MUST REMAIN",new Point(45,685),Imgproc.FONT_HERSHEY_SIMPLEX,.6,new Scalar(30,30,30),1);
        Point tl=title?new Point(400,250):new Point(350,250),br=title?new Point(550,490):new Point(610,380);
        Imgproc.rectangle(image,tl,br,new Scalar(245,245,245),-1);
        Imgproc.putText(image,title?"TITLE":"TABLE",new Point(tl.x+15,tl.y+40),Imgproc.FONT_HERSHEY_SIMPLEX,.55,new Scalar(25,25,25),1);
        return image;
    }
    private static JSONArray points(double x,double y,double w,double h)throws JSONException{return new JSONArray().put(new JSONArray(new double[]{x,y})).put(new JSONArray(new double[]{x+w,y})).put(new JSONArray(new double[]{x+w,y+h})).put(new JSONArray(new double[]{x,y+h}));}
    @Test public void automaticTinyTableAndTitleKeepTheFullPageInEveryMode()throws Exception{
        for(boolean title:new boolean[]{false,true}){
            Mat image=page(title),original=image.clone();
            try{
                for(String mode:new String[]{"color","clean","bw"})try(ScanProcessor.Result r=ScanProcessor.process(image,null,mode)){
                    assertFalse("high-confidence internal box must not discard the rest of the page",r.hasQuad);
                    assertNull(r.quad);assertEquals(image.size(),r.cropped.size());assertEquals(image.size(),r.enhanced.size());
                    assertEquals(0,Core.norm(image,r.cropped,Core.NORM_INF),0);
                    assertEquals("none",r.detection.getString("method"));assertEquals(0,r.detection.getDouble("confidence"),0);
                    assertEquals("candidate_too_small",r.detection.getString("rejection_reason"));
                    double ratio=r.detection.getDouble("candidate_area_ratio");assertTrue(ratio>0&&ratio<.10);
                    assertEquals(.10,r.detection.getDouble("minimum_area_ratio"),0);
                    JSONObject evidence=r.detection.getJSONObject("diagnostics");
                    assertEquals(4,evidence.getJSONArray("candidate_quad").length());assertTrue(evidence.getDouble("candidate_confidence")>.60);
                    assertTrue(r.warnings.toString().contains(WARNING));
                    if(mode.equals("color"))assertEquals(0,Core.norm(image,r.enhanced,Core.NORM_INF),0);
                }
                assertEquals(0,Core.norm(image,original,Core.NORM_INF),0);
            }finally{image.release();original.release();}
        }
        Mat diamond=new Mat(720,960,CvType.CV_8UC3,new Scalar(150,150,150));
        MatOfPoint ring=new MatOfPoint(new Point(480,216),new Point(696,360),new Point(480,504),new Point(264,360));
        try{
            Imgproc.fillConvexPoly(diamond,ring,new Scalar(245,245,245));
            Imgproc.putText(diamond,"PANEL",new Point(410,365),Imgproc.FONT_HERSHEY_SIMPLEX,.7,new Scalar(25,25,25),1);
            try(ScanProcessor.Result r=ScanProcessor.process(diamond,null,"color")){
                assertFalse("polygon9% must be rejected even though bbox18% would pass",r.hasQuad);
                double ratio=r.detection.getDouble("candidate_area_ratio");assertTrue(ratio>.08&&ratio<.10);
                assertEquals(diamond.size(),r.cropped.size());
                JSONArray q=r.detection.getJSONObject("diagnostics").getJSONArray("candidate_quad");
                double x0=960,y0=720,x1=0,y1=0;for(int i=0;i<4;i++){double x=q.getJSONArray(i).getDouble(0),y=q.getJSONArray(i).getDouble(1);x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}
                assertTrue((x1-x0)*(y1-y0)/(960*720)>.10);
            }
        }finally{diamond.release();ring.release();}
    }
    @Test public void explicitManualSmallQuadIsAllowedInEveryMode()throws Exception{
        for(boolean title:new boolean[]{false,true}){
            Mat image=page(title);
            try{
                JSONArray quad=title?points(400,250,150,240):points(350,250,260,130);
                for(String mode:new String[]{"color","clean","bw"})try(ScanProcessor.Result r=ScanProcessor.process(image,quad,mode)){
                    assertTrue(r.hasQuad);assertEquals("manual",r.detection.getString("method"));
                    assertEquals(title?151:261,r.cropped.cols());assertEquals(title?241:131,r.cropped.rows());
                    assertEquals(r.cropped.size(),r.enhanced.size());assertFalse(r.detection.has("rejection_reason"));
                    assertFalse(r.warnings.toString().contains(WARNING));
                }
            }finally{image.release();}
        }
    }
}
