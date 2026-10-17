package com.snapscan.offline;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;

/** Identical pixels to Android tests. All renderings are labeled; no fake original. */
public class BlueScanRegressionTest {
    static int checks;
    static void require(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception{
        nu.pattern.OpenCV.loadLocally();File root=new File(args[0]);JSONArray cases=new JSONArray();
        String[] names={"photo_with_ui","blue_mild","blue_medium","warm","black_border","shadow","blue_shadow_border","moire"};
        for(String name:names){
            File file=new File(root,"mobile/android-local/app/src/androidTest/assets/scan/android_blue_"+name+".png");
            MatOfByte bytes=new MatOfByte(Files.readAllBytes(file.toPath()));Mat image=Imgcodecs.imdecode(bytes,Imgcodecs.IMREAD_COLOR),before=image.clone();bytes.release();
            try(ScanProcessor.Result result=ScanProcessor.process(image,null,"clean")){
                require(result.hasQuad,name+" visible paper was not detected");
                double[][] ref=name.equals("photo_with_ui")?new double[][]{{144,29},{784,10},{809,1457},{209,1457}}:new double[][]{{166,0},{553,0},{585,884},{170,884}};
                if(name.contains("border")||name.equals("moire"))for(double[] point:ref){point[0]+=18;point[1]+=24;}
                Point[] p=new Point[4],q=new Point[4];for(int i=0;i<4;i++){p[i]=new Point(result.quad.getJSONArray(i).getDouble(0),result.quad.getJSONArray(i).getDouble(1));q[i]=new Point(ref[i][0],ref[i][1]);}
                MatOfPoint2f x=new MatOfPoint2f(p),y=new MatOfPoint2f(q);Mat intersection=new Mat();double overlap;
                try{double s=Imgproc.intersectConvexConvex(x,y,intersection);overlap=s/(Imgproc.contourArea(x)+Imgproc.contourArea(y)-s);}finally{x.release();y.release();intersection.release();}
                require(overlap>.90,name+" crop follows paper not UI frame: "+overlap);
                require(Core.norm(image,before,Core.NORM_INF)==0,"caller input unchanged");
                require(!result.detection.optBoolean("document_complete",false),"off-frame contents never reconstructed");
                if(result.detection.getString("method").equals("adaptive_paper_region")){
                    require(result.detection.getInt("background_pixels_whitened")==0,"relative masks never erase image pixels");
                    try(ScanProcessor.Result manual=ScanProcessor.process(image,result.quad,"color")){
                        require(Core.norm(result.cropped,manual.cropped,Core.NORM_INF)==0,"adaptive crop is exact unmasked perspective warp");
                    }
                }
                cases.put(new JSONObject().put("name",name).put("quad",result.quad).put("detection",result.detection).put("reference_iou",overlap));
            }finally{image.release();before.release();}
        }
        for(String name:new String[]{"wood_blue_mild","wood_blue_medium","wood_warm","wood_border","wood_combined","wood_moire"}){
            File file=new File(root,"mobile/android-local/app/src/androidTest/assets/scan/android_blue_"+name+".png");
            MatOfByte bytes=new MatOfByte(Files.readAllBytes(file.toPath()));Mat image=Imgcodecs.imdecode(bytes,Imgcodecs.IMREAD_COLOR),before=image.clone();bytes.release();
            try(ScanProcessor.Result result=ScanProcessor.process(image,null,"clean")){
                require(!result.hasQuad,name+" wood/glare must not become paper");
                require(Core.norm(image,before,Core.NORM_INF)==0,"negative input unchanged");
                cases.put(new JSONObject().put("name",name).put("source_kind","synthetic_wood_glare_no_document").put("has_quad",false).put("detection",result.detection));
            }finally{image.release();before.release();}
        }
        Mat large=new Mat(720,960,CvType.CV_8UC3,new Scalar(30,30,30));
        Imgproc.rectangle(large,new Point(35,35),new Point(925,685),new Scalar(235,235,235),-1);
        for(int y=130;y<650;y+=70)Imgproc.putText(large,"LARGE PAPER WITH TEXT",new Point(130,y),Imgproc.FONT_HERSHEY_SIMPLEX,1,new Scalar(30,30,30),2);
        try(ScanProcessor.Result result=ScanProcessor.process(large,null,"clean")){
            require(result.hasQuad,"large complete paper on black desk is not a screen-panel negative");
            require(!result.detection.getString("method").equals("adaptive_paper_region"),"old complete-document path stays unchanged");
            cases.put(new JSONObject().put("name","large_white_paper_black_desk").put("source_kind","synthetic_full_document_positive").put("quad",result.quad).put("detection",result.detection));
        }finally{large.release();}
        Files.write(new File(root,"reports/android_blue_scan_host.json").toPath(),new JSONObject().put("checks",checks).put("cases",cases).put("runtime","host OpenCV JNI; not Android").toString(2).getBytes(StandardCharsets.UTF_8));
        System.out.println("PASS "+checks+" screen-variant JNI checks");
    }
}
