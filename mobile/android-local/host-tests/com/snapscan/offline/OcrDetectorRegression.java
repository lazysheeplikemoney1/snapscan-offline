package com.snapscan.offline;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;

/** Actual detector probabilities from three approved OLD DEV pages, plus
 * synthetic negative/curved-band cases. No holdout image/model evaluation. */
public final class OcrDetectorRegression {
    static int checks;
    static void require(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static float[][] values(Mat probability){float[] flat=new float[(int)probability.total()];probability.get(0,0,flat);float[][] rows=new float[probability.rows()][probability.cols()];for(int i=0;i<rows.length;i++)System.arraycopy(flat,i*probability.cols(),rows[i],0,probability.cols());return rows;}
    public static void main(String[] args)throws Exception{
        nu.pattern.OpenCV.loadLocally();Path cache=Path.of(args[0]);JSONArray cases=new JSONArray();
        Mat curved=new Mat(180,800,CvType.CV_32F,new Scalar(.01));
        ArrayList<Point> points=new ArrayList<>();for(int x=40;x<760;x++)points.add(new Point(x,80+28*Math.sin(x/110.0)));
        MatOfPoint contour=new MatOfPoint();contour.fromList(points);Imgproc.polylines(curved,Collections.singletonList(contour),false,new Scalar(.95),6);contour.release();
        try{
            List<Point[]> result=OcrEngine.postprocess(values(curved),800,180);
            require(result.size()==1,"a confident curved text band must not be rejected because its rectangle includes whitespace");
            double low=Double.POSITIVE_INFINITY,high=Double.NEGATIVE_INFINITY;for(Point p:result.get(0)){low=Math.min(low,p.y);high=Math.max(high,p.y);}
            require(high-low<90,"curved contour padding must follow actual text-band area, not its tall whitespace-filled rectangle: "+(high-low));
        }
        finally{curved.release();}
        Mat weak=new Mat(100,500,CvType.CV_32F,new Scalar(.01));Imgproc.rectangle(weak,new Point(20,35),new Point(480,55),new Scalar(.4),-1);
        try{require(OcrEngine.postprocess(values(weak),500,100).isEmpty(),"box-score .5 threshold must still reject weak .4 regions");}finally{weak.release();}
        Mat blank=new Mat(100,500,CvType.CV_32F,new Scalar(.01));
        try{require(OcrEngine.postprocess(values(blank),500,100).isEmpty(),"blank detector map remains empty");}finally{blank.release();}
        JSONObject manifest=new JSONObject(Files.readString(cache.resolve("probability-manifest.json"),StandardCharsets.UTF_8));
        JSONArray samples=manifest.getJSONArray("samples");
        for(int i=0;i<samples.length();i++){
            JSONObject sample=samples.getJSONObject(i);int height=sample.getInt("height"),width=sample.getInt("width");
            byte[] bytes=Files.readAllBytes(cache.resolve(sample.getString("file")));require(bytes.length==height*width*4,"exact cached float32 shape");
            float[] flat=new float[height*width];ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(flat);
            float[][] probability=new float[height][width];for(int row=0;row<height;row++)System.arraycopy(flat,row*width,probability[row],0,width);
            List<Point[]> result=OcrEngine.postprocess(probability,width,height);
            require(result.size()==sample.getInt("expected_contour_score_boxes"),sample.getString("image_id")+" must retain all actual confident contour regions; got "+result.size());
            int top=0,bottom=0;for(Point[] box:result){double y=0;for(Point p:box)y+=p.y/4;if(y<height*.3)top++;if(y>height*.7)bottom++;}
            require(top>0&&bottom>0,"curved upper and lower page lines survive");
            cases.put(new JSONObject().put("image_id",sample.getString("image_id")).put("boxes",result.size()).put("upper_30_percent",top).put("lower_30_percent",bottom));
        }
        JSONObject report=new JSONObject().put("runtime","actual Java postprocess + host OpenCV JNI").put("checks",checks).put("holdout_read",false).put("cases",cases);
        Files.writeString(cache.resolve("java-detector-regression.json"),report.toString(2),StandardCharsets.UTF_8);
        System.out.println("PASS "+checks+" detector regressions on cached old DEV probabilities");
    }
}
