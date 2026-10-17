package com.snapscan.offline;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import org.json.*;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;

/** Evaluation only: unchanged production OCR and automatic clean-scan paths. */
public final class ScanBenchmarkHost {
    public static void main(String[] args) throws Exception {
        nu.pattern.OpenCV.loadLocally();
        JSONArray inputs = new JSONArray(Files.readString(Paths.get(args[1]), StandardCharsets.UTF_8));
        Path output = Paths.get(args[2]); Files.createDirectories(output);
        try (OcrEngine engine = new OcrEngine(new File(args[0]))) {
            for (int i = 0; i < inputs.length(); i++) {
                JSONObject item = inputs.getJSONObject(i), record = new JSONObject();
                String id = item.getString("image_id");
                MatOfByte bytes = new MatOfByte(Files.readAllBytes(Paths.get(item.getString("input"))));
                Mat image = Imgcodecs.imdecode(bytes, Imgcodecs.IMREAD_COLOR); bytes.release();
                if (image.empty()) throw new AssertionError("Unreadable input: " + id);
                try {
                    record.put("image_id", id).put("width", image.cols()).put("height", image.rows());
                    record.put("raw", recognize(engine, image));
                    try (ScanProcessor.Result scan = ScanProcessor.process(image, null, "clean")) {
                        record.put("clean", recognize(engine, scan.enhanced));
                        record.put("scan", new JSONObject().put("has_quad", scan.hasQuad)
                            .put("quad", scan.quad == null ? JSONObject.NULL : scan.quad)
                            .put("detection", scan.detection).put("quality", scan.quality).put("warnings", scan.warnings)
                            .put("width", scan.enhanced.cols()).put("height", scan.enhanced.rows()));
                        MatOfByte png = new MatOfByte();
                        try { Imgcodecs.imencode(".png", scan.enhanced, png); Files.write(output.resolve(id + "-clean.png"), png.toArray()); }
                        finally { png.release(); }
                    } catch (Exception failure) { record.put("scan_error", failure.toString()); }
                    Files.writeString(output.resolve(id + ".json"), record.toString(2), StandardCharsets.UTF_8);
                    JSONObject rawResult = record.getJSONObject("raw").optJSONObject("result");
                    System.out.println(id + " raw=" + (rawResult == null ? "ERROR" : rawResult.getJSONArray("boxes").length()));
                    System.out.flush();
                } finally { image.release(); }
            }
        }
    }
    static JSONObject recognize(OcrEngine engine, Mat image) throws Exception {
        long start = System.nanoTime();
        try { return new JSONObject().put("result", engine.recognize(image)).put("elapsed_ms", (System.nanoTime()-start)/1e6); }
        catch (Exception failure) { return new JSONObject().put("error", failure.toString()).put("elapsed_ms", (System.nanoTime()-start)/1e6); }
    }
}
