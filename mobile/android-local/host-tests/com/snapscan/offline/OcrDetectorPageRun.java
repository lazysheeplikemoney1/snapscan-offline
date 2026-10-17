package com.snapscan.offline;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;

/** One loaded engine, explicit approved manifest only. Even a zero-box result
 * is retained as evidence instead of being silently excluded from evaluation. */
public final class OcrDetectorPageRun {
    public static void main(String[] args)throws Exception{
        nu.pattern.OpenCV.loadLocally();Path output=Path.of(args[2]);Files.createDirectories(output);
        JSONArray inputs=new JSONObject(Files.readString(Path.of(args[1]),StandardCharsets.UTF_8)).getJSONArray("inputs");
        try(OcrEngine engine=new OcrEngine(new java.io.File(args[0]))){
            for(int i=0;i<inputs.length();i++){
                JSONObject item=inputs.getJSONObject(i);String id=item.getString("sample_id");
                MatOfByte encoded=new MatOfByte(Files.readAllBytes(Path.of(item.getString("input"))));Mat image=Imgcodecs.imdecode(encoded,Imgcodecs.IMREAD_COLOR);encoded.release();
                Mat before=image.clone();
                try{
                    JSONObject result=engine.recognize(image);if(Core.norm(image,before,Core.NORM_INF)!=0)throw new AssertionError("OCR mutated input");
                    result.put("source_image_id",id).put("source_split","dev").put("holdout_read",false).put("runtime","actual Java ORT inference; host OpenCV JNI; not a phone latency measurement");
                    Files.writeString(output.resolve(id+".json"),result.toString(2),StandardCharsets.UTF_8);
                    System.out.println("PAGE "+id+" boxes="+result.getJSONArray("boxes").length()+" chars="+result.getString("text").length());System.out.flush();
                }finally{image.release();before.release();}
            }
        }
    }
}
