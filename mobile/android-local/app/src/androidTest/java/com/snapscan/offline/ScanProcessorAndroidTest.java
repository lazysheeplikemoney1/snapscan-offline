package com.snapscan.offline;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.BeforeClass;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.json.JSONArray;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import static org.junit.Assert.*;

/** Actual Android JNI acceptance; fixtures are test-only and remain local. */
@RunWith(AndroidJUnit4.class)
public final class ScanProcessorAndroidTest {
    @BeforeClass public static void openCv(){assertTrue(OpenCVLoader.initLocal());}
    private static Mat fixture(String name)throws Exception{
        try(InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open(name);
            ByteArrayOutputStream bytes=new ByteArrayOutputStream()){
            byte[] buffer=new byte[65536];int n;while((n=input.read(buffer))!=-1)bytes.write(buffer,0,n);
            MatOfByte encoded=new MatOfByte(bytes.toByteArray());try{return Imgcodecs.imdecode(encoded,Imgcodecs.IMREAD_COLOR);}finally{encoded.release();}
        }
    }
    private static JSONArray quad(double[][] p)throws Exception{JSONArray a=new JSONArray();for(double[] xy:p)a.put(new JSONArray(xy));return a;}
    private static double iou(JSONArray a,double[][] reference)throws Exception{
        Point[] p=new Point[4],q=new Point[4];for(int i=0;i<4;i++){p[i]=new Point(a.getJSONArray(i).getDouble(0),a.getJSONArray(i).getDouble(1));q[i]=new Point(reference[i][0],reference[i][1]);}
        MatOfPoint2f first=new MatOfPoint2f(p),second=new MatOfPoint2f(q);Mat intersection=new Mat();try{double s=Imgproc.intersectConvexConvex(first,second,intersection);return s/(Imgproc.contourArea(first)+Imgproc.contourArea(second)-s);}finally{first.release();second.release();intersection.release();}
    }
    @Test public void realClippedWoodReceiptKeepsVisiblePaperAndOriginalPixels()throws Exception{
        Mat image=fixture("scan/receipt_clipped_wood.png"),original=image.clone();assertFalse(image.empty());
        try(ScanProcessor.Result result=ScanProcessor.process(image,null,"clean")){
            assertTrue(result.hasQuad);assertEquals("clipped_paper_region",result.detection.getString("method"));
            assertFalse(result.detection.getBoolean("document_complete"));assertTrue(result.detection.getBoolean("overlap_possible"));
            assertEquals(2,result.detection.getJSONArray("clipped_edges").length());assertEquals(885,result.cropped.rows());assertTrue(result.cropped.cols()<440);
            assertTrue(iou(result.quad,new double[][]{{166,0},{553,0},{585,884},{170,884}})>.94);
            assertTrue(result.detection.getInt("background_pixels_whitened")>0);assertTrue(result.enhanced.get(10,result.enhanced.cols()-1)[0]>235);
            assertEquals(0,Core.norm(image,original,Core.NORM_INF),0);
        }finally{image.release();original.release();}
    }
    @Test public void completeDevInvoiceKeepsTheOuterSheet()throws Exception{
        Mat image=fixture("scan/real-037.jpg");
        try(ScanProcessor.Result result=ScanProcessor.process(image,null,"clean")){
            assertTrue(result.hasQuad);assertTrue(iou(result.quad,new double[][]{{513,253},{3777,84},{3746,2533},{739,2646}})>.97);
            assertTrue(result.cropped.cols()>3000);assertTrue(result.cropped.rows()>2300);
        }finally{image.release();}
    }
    @Test public void threeModesRetainFineInkAndDoNotMutateInput()throws Exception{
        Mat image=new Mat(300,400,CvType.CV_8UC3,new Scalar(190,205,220));Imgproc.line(image,new Point(105,80),new Point(290,80),new Scalar(25,25,25),1);Imgproc.circle(image,new Point(200,170),15,new Scalar(30,30,200),2);Mat original=image.clone();
        JSONArray points=quad(new double[][]{{100,30},{300,30},{300,260},{100,260}});
        try{
            for(String mode:new String[]{"color","clean","bw"})try(ScanProcessor.Result r=ScanProcessor.process(image,points,mode)){
                assertEquals(231,r.cropped.rows());assertEquals(201,r.cropped.cols());assertTrue(r.enhanced.get(50,50)[0]<80);assertEquals("advisory_only",r.quality.getString("quality_gate"));
                if(mode.equals("color"))assertEquals(0,Core.norm(r.cropped,r.enhanced,Core.NORM_INF),0);
                if(mode.equals("clean")){double[] red=r.enhanced.get(140,115);assertTrue(red[2]>red[0]+70);}
            }
            assertEquals(0,Core.norm(image,original,Core.NORM_INF),0);
        }finally{image.release();original.release();}
    }
    @Test public void manualCrossingCannotSilentlyFallBackToAutomatic()throws Exception{
        Mat image=new Mat(300,400,CvType.CV_8UC3,new Scalar(200,200,200));boolean rejected=false;
        try{try(ScanProcessor.Result ignored=ScanProcessor.process(image,quad(new double[][]{{100,30},{300,260},{300,30},{100,260}}),"clean")){}catch(IllegalArgumentException expected){rejected=true;}assertTrue(rejected);}finally{image.release();}
    }
    @Test public void blankAndWoodGlareAreRejected()throws Exception{
        Mat image=new Mat(480,640,CvType.CV_8UC3,new Scalar(245,245,245));
        try{try(ScanProcessor.Result r=ScanProcessor.process(image,null,"color")){assertFalse(r.hasQuad);assertEquals(image.size(),r.cropped.size());}
            image.setTo(new Scalar(70,115,145));Imgproc.ellipse(image,new Point(20,240),new Size(80,120),0,0,360,new Scalar(231,242,248),-1);
            try(ScanProcessor.Result r=ScanProcessor.process(image,null,"color")){assertFalse(r.hasQuad);}
        }finally{image.release();}
    }
}
