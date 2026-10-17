package com.snapscan.offline;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;

/** Synthetic page interiors: a confident small box must never crop away a page. */
public final class SmallCandidateRegressionTest {
    private static int checks;
    private static void require(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    private static final String WARNING="自动候选区域小于整图的10%，已保留整图；请手动调整四角。";
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
    private static JSONArray points(double x,double y,double w,double h){return new JSONArray().put(new JSONArray(new double[]{x,y})).put(new JSONArray(new double[]{x+w,y})).put(new JSONArray(new double[]{x+w,y+h})).put(new JSONArray(new double[]{x,y+h}));}
    public static void main(String[] args)throws Exception{
        nu.pattern.OpenCV.loadLocally();JSONArray cases=new JSONArray();
        for(boolean title:new boolean[]{false,true}){
            Mat image=page(title),original=image.clone();
            try{
                for(String mode:new String[]{"color","clean","bw"})try(ScanProcessor.Result r=ScanProcessor.process(image,null,mode)){
                    require(!r.hasQuad,(title?"title":"table")+" internal candidate must retain full page, got "+r.detection);
                    require(r.quad==null,"rejected quad must be null");
                    require(r.cropped.size().equals(image.size()),"rejected candidate keeps full image geometry");
                    require(Core.norm(image,r.cropped,Core.NORM_INF)==0,"cropped image is exact source copy before enhancement");
                    require(r.enhanced.size().equals(image.size()),"all modes preserve full image geometry");
                    require(r.detection.getString("method").equals("none"),"rejection is not a successful auto detection");
                    require(r.detection.getString("rejection_reason").equals("candidate_too_small"),"explicit rejection reason");
                    double ratio=r.detection.getDouble("candidate_area_ratio");
                    require(ratio>0&&ratio<.10,"real polygon ratio retained");
                    require(r.detection.getDouble("minimum_area_ratio")==.10,"fixed area boundary");
                    JSONObject diagnostic=r.detection.getJSONObject("diagnostics");
                    require(diagnostic.getJSONArray("candidate_quad").length()==4,"candidate geometry retained for diagnostics");
                    require(diagnostic.getDouble("candidate_confidence")>.60,"confidence alone must not authorize a tiny crop");
                    require(r.warnings.toString().contains(WARNING),"visible manual-adjustment warning");
                    if(mode.equals("color"))require(Core.norm(image,r.enhanced,Core.NORM_INF)==0,"color fallback exact pixels");
                    cases.put(new JSONObject().put("scene",title?"internal_title":"internal_table").put("mode",mode).put("detection",r.detection));
                }
                JSONArray quad=title?points(400,250,150,240):points(350,250,260,130);
                try(ScanProcessor.Result r=ScanProcessor.process(image,quad,"color")){
                    require(r.hasQuad&&r.detection.getString("method").equals("manual"),"explicit small manual crop remains allowed");
                    require(r.cropped.cols()==(title?151:261)&&r.cropped.rows()==(title?241:131),"manual dimensions remain inclusive");
                    require(!r.detection.has("rejection_reason"),"manual crop is not rejected");
                }
                require(Core.norm(image,original,Core.NORM_INF)==0,"caller original is immutable");
            }finally{image.release();original.release();}
        }
        Mat diamond=new Mat(720,960,CvType.CV_8UC3,new Scalar(150,150,150));
        MatOfPoint ring=new MatOfPoint(new Point(480,216),new Point(696,360),new Point(480,504),new Point(264,360));
        try{
            Imgproc.fillConvexPoly(diamond,ring,new Scalar(245,245,245));
            Imgproc.putText(diamond,"PANEL",new Point(410,365),Imgproc.FONT_HERSHEY_SIMPLEX,.7,new Scalar(25,25,25),1);
            try(ScanProcessor.Result r=ScanProcessor.process(diamond,null,"color")){
                require(!r.hasQuad,"polygon9% must be rejected even when its bounding rectangle is18%");
                double ratio=r.detection.getDouble("candidate_area_ratio");require(ratio>.08&&ratio<.10,"polygon rather than bbox used");
                require(r.cropped.size().equals(diamond.size()),"small diamond preserves the full page");
                JSONArray q=r.detection.getJSONObject("diagnostics").getJSONArray("candidate_quad");
                double x0=960,y0=720,x1=0,y1=0;for(int i=0;i<4;i++){double x=q.getJSONArray(i).getDouble(0),y=q.getJSONArray(i).getDouble(1);x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}
                require((x1-x0)*(y1-y0)/(960*720)>.10,"bbox would wrongly allow this internal panel");
                cases.put(new JSONObject().put("scene","polygon9_bbox18_percent").put("detection",r.detection));
            }
        }finally{diamond.release();ring.release();}
        JSONObject report=new JSONObject().put("scope","scanner regression after frozen independent evaluation; synthetic only, no OCR rerun")
            .put("checks",checks).put("cases",cases).put("runtime","host OpenCV JNI; Android instrumentation pending");
        File target=new File(args[0],"reports/android_small_candidate_host.json");Files.write(target.toPath(),report.toString(2).getBytes(StandardCharsets.UTF_8));
        System.out.println("PASS "+checks+" small-candidate scanner checks");
    }
}
