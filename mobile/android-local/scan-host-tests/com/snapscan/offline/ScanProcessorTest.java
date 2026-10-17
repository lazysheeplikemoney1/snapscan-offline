package com.snapscan.offline;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.json.JSONArray;
import org.json.JSONObject;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import org.opencv.imgcodecs.Imgcodecs;

/** Real native OpenCV host checks. Android 4.12 is compiled separately. */
public final class ScanProcessorTest {
    static int checks;
    static void require(boolean ok,String message) { checks++; if(!ok) throw new AssertionError(message); }
    static Mat read(File path)throws Exception { MatOfByte data=new MatOfByte(Files.readAllBytes(path.toPath()));try{return Imgcodecs.imdecode(data,Imgcodecs.IMREAD_COLOR);}finally{data.release();} }
    static void write(File path,Mat image)throws Exception { MatOfByte data=new MatOfByte();try{require(Imgcodecs.imencode(".png",image,data),"encode diagnostic");Files.write(path.toPath(),data.toArray());}finally{data.release();} }
    static JSONArray points(double[][] p) { JSONArray a=new JSONArray(); for(double[] v:p)a.put(new JSONArray(v));return a; }
    static double overlap(JSONArray a, double[][] b) throws Exception {
        Point[] p=new Point[4],q=new Point[4];
        for(int i=0;i<4;i++){p[i]=new Point(a.getJSONArray(i).getDouble(0),a.getJSONArray(i).getDouble(1));q[i]=new Point(b[i][0],b[i][1]);}
        MatOfPoint2f x=new MatOfPoint2f(p),y=new MatOfPoint2f(q);Mat intersection=new Mat();
        double s=Imgproc.intersectConvexConvex(x,y,intersection);
        double answer=s/(Math.abs(Imgproc.contourArea(x))+Math.abs(Imgproc.contourArea(y))-s);
        x.release();y.release();intersection.release();return answer;
    }
    static Mat scene(double[][] q,int background) {
        Mat paper=new Mat(600,400,CvType.CV_8UC3,new Scalar(240,240,240));
        Imgproc.putText(paper,"RECEIPT",new Point(35,65),Imgproc.FONT_HERSHEY_SIMPLEX,1.1,new Scalar(35,35,35),2);
        for(int i=0;i<9;i++)Imgproc.putText(paper,"ITEM 12.50",new Point(25,130+i*43),Imgproc.FONT_HERSHEY_SIMPLEX,.55,new Scalar(40,40,40),1);
        MatOfPoint2f src=new MatOfPoint2f(new Point(0,0),new Point(399,0),new Point(399,599),new Point(0,599));
        Point[] dest=new Point[4];for(int i=0;i<4;i++)dest[i]=new Point(q[i][0],q[i][1]);
        MatOfPoint2f dst=new MatOfPoint2f(dest);Mat h=Imgproc.getPerspectiveTransform(src,dst),fg=new Mat(),mask=new Mat(),ones=new Mat(600,400,CvType.CV_8U,new Scalar(255));
        Imgproc.warpPerspective(paper,fg,h,new Size(960,720));Imgproc.warpPerspective(ones,mask,h,new Size(960,720));
        Mat image=new Mat(720,960,CvType.CV_8UC3,new Scalar(background,background,background));fg.copyTo(image,mask);
        paper.release();src.release();dst.release();h.release();fg.release();mask.release();ones.release();return image;
    }
    static void manualAndModes() throws Exception {
        Mat image=new Mat(300,400,CvType.CV_8UC3,new Scalar(190,205,220));
        Imgproc.line(image,new Point(105,80),new Point(290,80),new Scalar(25,25,25),1);
        Imgproc.circle(image,new Point(200,170),15,new Scalar(30,30,200),2);
        Mat original=image.clone();JSONArray q=points(new double[][]{{100,30},{300,30},{300,260},{100,260}});
        for(String mode:new String[]{"color","clean","bw"})try(ScanProcessor.Result r=ScanProcessor.process(image,q,mode)){
            require(r.hasQuad,"manual quad found");require(r.cropped.rows()==231&&r.cropped.cols()==201,"inclusive geometry");
            require(r.enhanced.size().equals(r.cropped.size()),"mode preserves dimensions");
            require(r.enhanced.get(50,50)[0]<80,"one-pixel source ink is retained");
            require(r.quality.getString("quality_gate").equals("advisory_only"),"quality advisory");
            if(mode.equals("color")) require(Core.norm(r.cropped,r.enhanced,Core.NORM_INF)==0,"color exact copy");
            if(mode.equals("clean")){double[] stamp=r.enhanced.get(140,115);require(stamp[2]>stamp[0]+70,"red stamp retained");}
            if(mode.equals("bw")){Mat gray=new Mat();Imgproc.cvtColor(r.enhanced,gray,Imgproc.COLOR_BGR2GRAY);byte[] b=new byte[(int)gray.total()];gray.get(0,0,b);boolean binary=true;for(byte v:b)binary&=(v==0||(v&255)==255);gray.release();require(binary,"bw is binary");}
        }
        require(Core.norm(image,original,Core.NORM_INF)==0,"caller pixels immutable");
        JSONArray shifted=points(new double[][]{{300,30},{300,260},{100,260},{100,30}});
        try(ScanProcessor.Result r=ScanProcessor.process(image,shifted,"color")){require(r.cropped.rows()==231&&r.cropped.cols()==201,"cyclic manual ring normalizes to TL/TR/BR/BL before warping");}
        for(JSONArray bad:new JSONArray[]{points(new double[][]{{100,30},{300,260},{300,30},{100,260}}),points(new double[][]{{-1,30},{300,30},{300,260},{100,260}})}){
            boolean failed=false;try(ScanProcessor.Result ignored=ScanProcessor.process(image,bad,"clean")){}catch(IllegalArgumentException expected){failed=true;}require(failed,"invalid manual quad rejected without autoscan fallback");
        }
        image.release();original.release();
    }
    static void negatives()throws Exception {
        for(int kind=0;kind<4;kind++){
            Mat image=new Mat(480,640,CvType.CV_8UC3,new Scalar(245,245,245));
            if(kind==0){Imgproc.rectangle(image,new Point(200,150),new Point(500,350),new Scalar(25,25,25),2);for(int i=0;i<4;i++)Imgproc.putText(image,"ONLY TEXT BLOCK",new Point(215,190+i*40),Imgproc.FONT_HERSHEY_SIMPLEX,.6,new Scalar(20,20,20),1);}
            if(kind==1){byte[] bytes=new byte[480*640*3];new java.util.Random(20261016).nextBytes(bytes);image.put(0,0,bytes);}
            if(kind==2){for(int y=0;y<480;y+=12)for(int x=0;x<640;x+=12)Imgproc.rectangle(image,new Point(x,y),new Point(x+11,y+11),new Scalar(((x/12+y/12)%2)*180+35,((x/12+y/12)%2)*180+35,((x/12+y/12)%2)*180+35),-1);}
            if(kind==3){image.setTo(new Scalar(70,115,145));Imgproc.ellipse(image,new Point(20,240),new Size(80,120),0,0,360,new Scalar(231,242,248),-1);}
            try(ScanProcessor.Result r=ScanProcessor.process(image,null,"color")){require(!r.hasQuad,"text/noise/checkerboard/wood glare negative "+kind);}image.release();
        }
    }
    static void partialVariants()throws Exception {
        for(int orientation=0;orientation<2;orientation++){
            Mat image=new Mat(640,480,CvType.CV_8UC3,new Scalar(70,115,145));MatOfPoint paper=new MatOfPoint(new Point(125,0),new Point(342,0),new Point(363,639),new Point(135,639));Imgproc.fillConvexPoly(image,paper,new Scalar(225,225,225));paper.release();
            for(int y=30;y<620;y+=40)Imgproc.putText(image,"ITEM 12.50",new Point(155,y),Imgproc.FONT_HERSHEY_SIMPLEX,.4,new Scalar(45,45,45),1);
            if(orientation==1)Core.rotate(image,image,Core.ROTATE_90_CLOCKWISE);
            try(ScanProcessor.Result r=ScanProcessor.process(image,null,"bw")){require(r.hasQuad,"synthetic partial variant");JSONArray edges=r.detection.getJSONArray("clipped_edges");require(edges.length()==2,"two frame cuts retained");require(edges.toString().contains(orientation==0?"top":"left"),"orientation independent frame labels");}
            image.release();
        }
    }
    public static void main(String[] args) throws Exception {
        nu.pattern.OpenCV.loadLocally();File root=new File(args[0]);File output=new File(root,"output/scan/android-local");output.mkdirs();
        manualAndModes();negatives();partialVariants();
        double[][] complete={{210,100},{740,135},{690,640},{140,590}};
        for(int bg:new int[]{35,225}){Mat im=scene(complete,bg);try(ScanProcessor.Result r=ScanProcessor.process(im,null,"clean")){require(r.hasQuad,"complete paper detected bg="+bg);require(overlap(r.quad,complete)>.93,"full paper preserved");}im.release();}
        double[][] diamond={{400,70},{780,360},{460,660},{100,380}};Mat rotated=scene(diamond,35);try(ScanProcessor.Result r=ScanProcessor.process(rotated,null,"color")){require(r.hasQuad&&overlap(r.quad,diamond)>.93,"diamond has unique corners");}rotated.release();
        Mat blank=new Mat(480,640,CvType.CV_8UC3,new Scalar(245,245,245));try(ScanProcessor.Result r=ScanProcessor.process(blank,null,"color")){require(!r.hasQuad,"blank rejected");require(r.cropped.size().equals(blank.size()),"reject preserves source geometry");}blank.release();
        Mat fixture=read(new File(root,"backend/tests/fixtures/receipt_clipped_wood.png"));require(!fixture.empty(),"feedback fixture loaded locally");Mat copy=fixture.clone();
        JSONObject report=new JSONObject();long started=System.nanoTime();
        try(ScanProcessor.Result r=ScanProcessor.process(fixture,null,"clean")){
            require(r.hasQuad,"clipped wood receipt detected");require(r.detection.getString("method").equals("clipped_paper_region"),"color region fallback selected");
            require(!r.detection.getBoolean("document_complete"),"partial document flagged");require(r.detection.getJSONArray("clipped_edges").length()==2,"both frame cuts labelled");
            require(r.detection.getBoolean("overlap_possible"),"connected lower sheet acknowledged");require(overlap(r.quad,new double[][]{{166,0},{553,0},{585,884},{170,884}})>.94,"visible-paper geometry excludes wood glare");
            require(r.cropped.rows()==885&&r.cropped.cols()<440,"no off-frame content reconstructed");
            require(r.enhanced.get(10,r.enhanced.cols()-1)[0]>235,"provable background wedge whitened");
            write(new File(output,"feedback-cropped.png"),r.cropped);write(new File(output,"feedback-clean.png"),r.enhanced);
            Mat overlay=fixture.clone();Point[] p=new Point[4];for(int i=0;i<4;i++)p[i]=new Point(r.quad.getJSONArray(i).getDouble(0),r.quad.getJSONArray(i).getDouble(1));MatOfPoint ring=new MatOfPoint(p);Imgproc.polylines(overlay,java.util.Collections.singletonList(ring),true,new Scalar(30,220,30),3);write(new File(output,"feedback-quad.png"),overlay);overlay.release();ring.release();
            report.put("feedback",new JSONObject().put("quad",r.quad).put("detection",r.detection).put("size",new JSONArray(new int[]{r.cropped.rows(),r.cropped.cols()})).put("host_ms",(System.nanoTime()-started)/1e6));
        }
        require(Core.norm(fixture,copy,Core.NORM_INF)==0,"automatic scan leaves original intact");fixture.release();copy.release();
        JSONArray dev=new JSONArray();
        double[][][] references={{{374,532},{3398,549},{3387,2438},{424,2511}},{{513,253},{3777,84},{3746,2533},{739,2646}},{{141,1234},{2891,1486},{2677,3212},{32,2926}},{{85,365},{3630,232},{3612,2577},{279,2685}}};
        String[] ids={"real-036.jpg","real-037.jpg","real-043.jpg","real-056.jpg"};
        for(int i=0;i<ids.length;i++){Mat im=read(new File(root,"data/real/dev/dev/222/"+ids[i]));try(ScanProcessor.Result r=ScanProcessor.process(im,null,"clean")){require(r.hasQuad,"complete DEV found "+ids[i]);double score=overlap(r.quad,references[i]);require(score>.97,"DEV outer paper "+ids[i]);dev.put(new JSONObject().put("image_id",ids[i]).put("reference_iou",score).put("method",r.detection.getString("method")));if(i==1)write(new File(output,"dev037-clean.png"),r.enhanced);}im.release();}
        report.put("dev",dev).put("checks_passed",checks).put("runtime","Windows JVM + OpenPnP OpenCV 4.9.0 host JNI; Android 4.12 compiled separately").put("android_runtime_executed",false).put("phone_latency_claim",false).put("private_text_saved",false);
        Files.write(new File(root,"reports/android_scan_host.json").toPath(),report.toString(2).getBytes(StandardCharsets.UTF_8));
        System.out.println("PASS "+checks+" native OpenCV scan contracts; report reports/android_scan_host.json");
    }
}
