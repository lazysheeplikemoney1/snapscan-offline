package com.snapscan.offline;

import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;

/** Real JNI baseline; no assertions that a screenshot is the original photo. */
public class BlueScanProbe {
    public static void main(String[] args)throws Exception {
        nu.pattern.OpenCV.loadLocally();JSONArray rows=new JSONArray();
        for(int i=1;i<args.length;i++){
            File source=new File(args[i]);MatOfByte bytes=new MatOfByte(Files.readAllBytes(source.toPath()));
            Mat image=Imgcodecs.imdecode(bytes,Imgcodecs.IMREAD_COLOR),before=image.clone();bytes.release();
            try(ScanProcessor.Result result=ScanProcessor.process(image,null,"color")){
                rows.put(new JSONObject().put("file",source.getName()).put("has_quad",result.hasQuad).put("detection",result.detection)
                    .put("source_size",new JSONArray(new int[]{image.rows(),image.cols()})).put("input_unchanged",Core.norm(image,before,Core.NORM_INF)==0));
            }finally{image.release();before.release();}
        }
        Files.write(new File(args[0]).toPath(),rows.toString(2).getBytes(StandardCharsets.UTF_8));
        System.out.println(rows.toString());
    }
}
