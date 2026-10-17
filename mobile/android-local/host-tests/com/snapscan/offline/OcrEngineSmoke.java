package com.snapscan.offline;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import org.json.JSONObject;
import org.opencv.core.Core;
import org.opencv.core.Mat;
import org.opencv.core.MatOfByte;
import org.opencv.imgcodecs.Imgcodecs;

/** Real JVM native inference, not a mock. Never uses the locked test set. */
public final class OcrEngineSmoke {
    public static void main(String[] args) throws Exception {
        nu.pattern.OpenCV.loadLocally();
        byte[] bytes=Files.readAllBytes(Paths.get(args[1]));
        MatOfByte encoded=new MatOfByte(bytes);
        Mat image=Imgcodecs.imdecode(encoded,Imgcodecs.IMREAD_COLOR);
        encoded.release();
        Mat before=image.clone();
        try(OcrEngine engine=new OcrEngine(new File(args[0]))) {
            long started=System.nanoTime();
            JSONObject result=engine.recognize(image);
            double elapsedMs=(System.nanoTime()-started)/1e6;
            if(result.getString("text").trim().isEmpty() || result.getJSONArray("boxes").length()<10)
                throw new AssertionError("Real DEV OCR did not produce usable text");
            if(Core.norm(image,before,Core.NORM_INF)!=0) throw new AssertionError("OCR modified caller's Mat");
            if(!"rec_head_v1".equals(result.getJSONObject("model").getString("id"))) throw new AssertionError("Wrong model");
            for(int i=0;i<result.getJSONArray("boxes").length();i++) {
                JSONObject line=result.getJSONArray("boxes").getJSONObject(i);
                if(line.getDouble("score")<.5 || line.getDouble("score")>1) throw new AssertionError("Invalid confidence");
                if(line.getJSONArray("box").length()!=4) throw new AssertionError("OCR box must have four corners");
                for(int corner=0;corner<4;corner++) {
                    org.json.JSONArray point=line.getJSONArray("box").getJSONArray(corner);
                    double x=point.getDouble(0),y=point.getDouble(1);
                    if(!Double.isFinite(x) || !Double.isFinite(y) || x<0 || y<0 || x>image.cols() || y>image.rows())
                        throw new AssertionError("OCR box not in caller's original pixel coordinates");
                }
            }
            result.put("host_elapsed_ms",elapsedMs).put("host_opencv_version",Core.VERSION)
                .put("input_width",image.cols()).put("input_height",image.rows()).put("caller_input_unchanged",true);
            Files.write(Paths.get(args[2]),result.toString(2).getBytes(StandardCharsets.UTF_8));
            System.out.println("PASS real Java DEV inference: "+result.getJSONArray("boxes").length()+" boxes; "+result.getString("text").length()+" characters; "+elapsedMs+" ms HOST ONLY");
        } finally {image.release();before.release();}
    }
}
