package com.snapscan.offline;

import java.lang.reflect.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;

/** Independent OLD DEV diagnosis of actual crop/CTC scores before filtering.
 * Reflects existing pipeline boundaries without adding a production debug API.
 */
public final class OcrLineDiagnostics {
    static Method method(String name,Class<?>... args)throws Exception{Method m=OcrEngine.class.getDeclaredMethod(name,args);m.setAccessible(true);return m;}
    public static void main(String[] args)throws Exception{
        nu.pattern.OpenCV.loadLocally();Path output=Path.of(args[2]);Files.createDirectories(output);
        MatOfByte bytes=new MatOfByte(Files.readAllBytes(Path.of(args[1])));Mat original=Imgcodecs.imdecode(bytes,Imgcodecs.IMREAD_COLOR);bytes.release();
        try(OcrEngine engine=new OcrEngine(new java.io.File(args[0]))){
            Object prepared=method("prepare",Mat.class).invoke(null,original);Field f=prepared.getClass().getDeclaredField("image");f.setAccessible(true);Mat image=(Mat)f.get(prepared);
            try{
                @SuppressWarnings("unchecked") List<Point[]> boxes=(List<Point[]>)method("detect",Mat.class).invoke(engine,image);
                method("sortBoxes",List.class).invoke(null,boxes);JSONArray rows=new JSONArray();int accepted=0;
                for(int i=0;i<boxes.size();i++){
                    Mat crop=(Mat)method("crop",Mat.class,Point[].class).invoke(null,image,boxes.get(i));
                    try{
                        boolean rotated=(Boolean)method("rotate180IfNeeded",Mat.class).invoke(engine,crop);
                        OcrMath.Decoded decoded=(OcrMath.Decoded)method("recognizeLine",Mat.class).invoke(engine,crop);
                        boolean keep=decoded.score>=.5&&!decoded.text.isEmpty();if(keep)accepted++;
                        JSONArray coordinates=new JSONArray();for(Point p:boxes.get(i))coordinates.put(new JSONArray().put(p.x).put(p.y));
                        Path file=output.resolve(String.format("crop-%02d.png",i));Imgcodecs.imwrite(file.toString(),crop);
                        JSONObject row=new JSONObject().put("box_index",i).put("box_in_prepared_pixels",coordinates).put("crop_path",file.toString()).put("crop_width",crop.cols()).put("crop_height",crop.rows()).put("ctc_input_width",OcrMath.recWidth(crop.cols(),crop.rows())).put("score",decoded.score).put("text",decoded.text).put("accepted_at_05",keep).put("rotation180",rotated);
                        if(rotated&&!keep){Core.rotate(crop,crop,Core.ROTATE_180);OcrMath.Decoded originalDirection=(OcrMath.Decoded)method("recognizeLine",Mat.class).invoke(engine,crop);Path before=output.resolve(String.format("crop-%02d-original-direction.png",i));Imgcodecs.imwrite(before.toString(),crop);row.put("original_direction_crop",before.toString()).put("original_direction_score",originalDirection.score).put("original_direction_text",originalDirection.text);}
                        rows.put(row);
                    }finally{crop.release();}
                }
                Files.writeString(output.resolve("lines.json"),new JSONObject().put("source",args[1]).put("source_split","dev").put("holdout_read",false).put("detected_boxes",boxes.size()).put("accepted_at_05",accepted).put("lines",rows).toString(2),StandardCharsets.UTF_8);
                System.out.println("LINES "+boxes.size()+" detected; "+accepted+" accepted at unchanged .5");
            }finally{image.release();}
        }finally{original.release();}
    }
}
