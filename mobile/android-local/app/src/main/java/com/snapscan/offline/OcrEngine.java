package com.snapscan.offline;

import android.content.Context;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.TensorInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import org.opencv.core.Core;
import org.opencv.core.CvType;
import org.opencv.core.Mat;
import org.opencv.core.MatOfPoint;
import org.opencv.core.MatOfPoint2f;
import org.opencv.core.Point;
import org.opencv.core.Rect;
import org.opencv.core.RotatedRect;
import org.opencv.core.Scalar;
import org.opencv.core.Size;
import org.opencv.imgproc.Imgproc;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.FloatBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** Fully local PP-OCRv4 detector + existing direction model + rec_head_v1.
 *
 * Java/OpenCV port of RapidOCR 1.4.4's Apache-2.0 pipeline. No HTTP server,
 * Python runtime, remote model loading, input-image mutation, or input ownership.
 * Caller must load OpenCV and run recognize() on a worker thread.
 */
public final class OcrEngine implements AutoCloseable {
    private static final String MODEL_ID = "rec_head_v1";
    private static final String REC_SHA = "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe";
    private static final String[] FILES = {"det_fp32.onnx", "rec_head_v1.onnx", "cls.onnx", "characters.json"};
    private static final String[] SHAS = {
            "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9", REC_SHA,
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
            "4582857f6d97580c67a8ac5329ecb58b97871fad353d95b55bfce280d8fab4ba"};
    private final OrtEnvironment environment;
    private OrtSession detector, recognizer, classifier;
    private final String[] dictionary;
    private boolean closed;

    private interface AssetSource { InputStream open(String name) throws Exception; }

    public OcrEngine(Context context) throws Exception {
        this(name -> context.getAssets().open("models/" + name));
    }

    /** Desktop parity harness uses the exact same implementation and weights. */
    public OcrEngine(File modelDirectory) throws Exception {
        this(name -> new FileInputStream(new File(modelDirectory, name)));
    }

    private OcrEngine(AssetSource assets) throws Exception {
        JSONObject manifest = new JSONObject(new String(read(assets, "model_manifest.json"), StandardCharsets.UTF_8));
        if (!MODEL_ID.equals(manifest.getJSONObject("model").getString("id")) ||
                !REC_SHA.equals(manifest.getJSONObject("model").getString("sha256")))
            throw new IllegalArgumentException("OCR model manifest does not select rec_head_v1");
        byte[][] contents = new byte[FILES.length][];
        for (int index = 0; index < FILES.length; index++) {
            contents[index] = read(assets, FILES[index]);
            JSONObject entry = manifest.getJSONObject("files").getJSONObject(FILES[index]);
            if (!SHAS[index].equals(sha256(contents[index])) || !SHAS[index].equals(entry.getString("sha256")) ||
                    contents[index].length != entry.getLong("bytes"))
                throw new IllegalArgumentException("OCR asset hash mismatch: " + FILES[index]);
        }
        JSONArray symbols = new JSONArray(new String(contents[3], StandardCharsets.UTF_8));
        if (symbols.length() != 6625) throw new IllegalArgumentException("CTC dictionary must contain 6625 entries");
        dictionary = new String[symbols.length()];
        for (int index = 0; index < dictionary.length; index++) dictionary[index] = symbols.getString(index);
        if (!"blank".equals(dictionary[0]) || !" ".equals(dictionary[6624]))
            throw new IllegalArgumentException("CTC blank/space contract mismatch");
        environment = OrtEnvironment.getEnvironment();
        try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
            options.setIntraOpNumThreads(4);
            options.setInterOpNumThreads(1);
            options.setCPUArenaAllocator(false);
            options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
            detector = environment.createSession(contents[0], options);
            recognizer = environment.createSession(contents[1], options);
            classifier = environment.createSession(contents[2], options);
            checkSession(detector, 4, 1);
            checkSession(recognizer, 3, dictionary.length);
            checkSession(classifier, 2, 2);
            // ORT Java 1.23.2 decodes metadata through JNI modified UTF-8;
            // supplementary CJK (e.g. U+231C9) can corrupt adjacent separators.
            // Use the SHA-verified JSON exported from ONNX by Python instead.
        } catch (Exception exception) {
            close();
            throw exception;
        }
    }

    private static void checkSession(OrtSession session, int outputRank, int lastDimension) throws Exception {
        if (session.getNumInputs() != 1 || !session.getInputNames().contains("x") || session.getNumOutputs() != 1)
            throw new IllegalArgumentException("Unexpected ONNX inputs/outputs");
        TensorInfo input = (TensorInfo) session.getInputInfo().get("x").getInfo();
        TensorInfo output = (TensorInfo) session.getOutputInfo().values().iterator().next().getInfo();
        long[] inputShape = input.getShape(), shape = output.getShape();
        if (inputShape.length != 4 || inputShape[1] != 3 || shape.length != outputRank ||
                (outputRank != 4 && shape[shape.length - 1] != lastDimension) ||
                (outputRank == 4 && shape[1] != 1))
            throw new IllegalArgumentException("Unexpected OCR tensor shapes");
    }

    public JSONObject modelInfo() {
        try { return new JSONObject().put("id", MODEL_ID).put("sha256", REC_SHA); }
        catch (Exception exception) { throw new IllegalStateException(exception); }
    }

    public synchronized JSONObject recognize(Mat bgr) throws Exception {
        if (closed) throw new IllegalStateException("OCR engine is closed");
        if (bgr == null || bgr.empty() || bgr.type() != CvType.CV_8UC3)
            throw new IllegalArgumentException("OCR input must be a nonempty CV_8UC3 BGR image");
        Prepared prepared = prepare(bgr);
        try {
            List<Point[]> boxes = detect(prepared.image);
            sortBoxes(boxes);
            JSONArray results = new JSONArray();
            int rotated = 0;
            for (Point[] box : boxes) {
                Mat crop = crop(prepared.image, box);
                try {
                    if (rotate180IfNeeded(crop)) rotated++;
                    OcrMath.Decoded decoded = recognizeLine(crop);
                    if (decoded.score < .5 || decoded.text.isEmpty()) continue;
                    JSONArray coordinates = new JSONArray();
                    for (Point point : box) coordinates.put(new JSONArray()
                            .put(clamp(point.x * prepared.ratioX, 0, bgr.cols()))
                            .put(clamp((point.y - prepared.paddingTop) * prepared.ratioY, 0, bgr.rows())));
                    results.put(new JSONObject().put("box", coordinates).put("text", decoded.text).put("score", decoded.score));
                } finally { crop.release(); }
            }
            return TextLayout.apply(new JSONObject().put("text", "").put("boxes", results).put("model", modelInfo())
                    .put("engine", "onnxruntime-android-cpu").put("direction_classifier", "existing_ppocr_mobile_v2")
                    .put("direction_rotations_180", rotated));
        } finally { prepared.image.release(); }
    }

    private static final class Prepared {
        Mat image;
        double ratioX, ratioY;
        int paddingTop;
    }

    private static Prepared prepare(Mat original) {
        Prepared result = new Prepared();
        Mat scaled = original.clone();
        try {
        if (Math.max(scaled.rows(), scaled.cols()) > 2000) {
            int[] shape = OcrMath.resize32(scaled.rows(), scaled.cols(), 2000, false);
            Imgproc.resize(scaled, scaled, new Size(shape[1], shape[0]));
        }
        if (Math.min(scaled.rows(), scaled.cols()) < 30) {
            int[] shape = OcrMath.resize32(scaled.rows(), scaled.cols(), 30, true);
            Imgproc.resize(scaled, scaled, new Size(shape[1], shape[0]));
        }
        result.ratioX = original.cols() / (double) scaled.cols();
        result.ratioY = original.rows() / (double) scaled.rows();
        if (scaled.rows() <= 30 || scaled.cols() / (double) scaled.rows() > 8) {
            int newHeight = Math.max(scaled.cols() / 8, 30) * 2;
            result.paddingTop = Math.abs(newHeight - scaled.rows()) / 2;
            Core.copyMakeBorder(scaled, scaled, result.paddingTop, result.paddingTop, 0, 0, Core.BORDER_CONSTANT, new Scalar(0,0,0));
        }
        result.image = scaled;
        return result;
        } catch (RuntimeException exception) {scaled.release();throw exception;}
    }

    private List<Point[]> detect(Mat source) throws Exception {
        int[] shape = OcrMath.resize32(source.rows(), source.cols(), 736, true);
        if ((long)shape[0] * shape[1] > 8_000_000)
            throw new IllegalArgumentException("文档过于狭长，请分段拍摄或缩小选区");
        Mat resized = new Mat();
        float[][] probabilities;
        try {
            Imgproc.resize(source, resized, new Size(shape[1], shape[0]));
            try (OnnxTensor tensor = tensor(resized, resized.cols());
                 OrtSession.Result result = detector.run(Collections.singletonMap("x", tensor))) {
                probabilities = ((float[][][][])result.get(0).getValue())[0][0];
            }
        } finally { resized.release(); }
        return postprocess(probabilities,source.cols(),source.rows());
    }

    /** DB postprocessing boundary shared by inference and cached-map regressions. */
    static List<Point[]> postprocess(float[][] probabilities,int sourceWidth,int sourceHeight) throws Exception {
        int height = probabilities.length, width = probabilities[0].length;
        Mat probability = new Mat(height, width, CvType.CV_32F);
        Mat bitmap = new Mat(), kernel = Mat.ones(2,2,CvType.CV_8U), hierarchy = new Mat();
        List<MatOfPoint> contours = new ArrayList<>();
        try {
            float[] flattened = new float[height * width];
            for (int row = 0; row < height; row++) System.arraycopy(probabilities[row],0,flattened,row*width,width);
            probability.put(0,0,flattened);
            Imgproc.threshold(probability, bitmap, .3, 255, Imgproc.THRESH_BINARY);
            bitmap.convertTo(bitmap,CvType.CV_8U);
            Imgproc.dilate(bitmap,bitmap,kernel);
            Imgproc.findContours(bitmap,contours,hierarchy,Imgproc.RETR_LIST,Imgproc.CHAIN_APPROX_SIMPLE);
            List<Point[]> boxes = new ArrayList<>();
            for (int index=0; index < Math.min(contours.size(),1000); index++) {
                MatOfPoint2f contour = new MatOfPoint2f(contours.get(index).toArray());
                RotatedRect rectangle;double perimeter;
                try { rectangle=Imgproc.minAreaRect(contour);perimeter=Imgproc.arcLength(contour,true); }
                finally { contour.release(); }
                if (Math.min(rectangle.size.width,rectangle.size.height) < 3) continue;
                Point[] corners=ordered(rectangle);
                // A curved book line's enclosing rectangle contains whitespace
                // that dilutes a confident text contour. Match DB's "slow"
                // polygon scoring while retaining the same .5 threshold.
                if (polygonScore(probability,contours.get(index).toArray()) < .5) continue;
                double area=Math.abs(Imgproc.contourArea(contours.get(index)));
                if(!Double.isFinite(area)||!Double.isFinite(perimeter)||area<=0||perimeter<=0)continue;
                // Measure real text-band thickness. For a curved line the
                // rectangle's area includes broad whitespace and can otherwise
                // expand the crop into several neighboring text lines.
                double distance=area*1.6/perimeter;if(!Double.isFinite(distance))continue;
                double[][] expanded=OcrMath.unclipRectangleByDistance(toArray(corners),distance);
                Point[] points=new Point[4];
                for (int j=0;j<4;j++) points[j]=new Point(expanded[j][0],expanded[j][1]);
                MatOfPoint2f polygon=new MatOfPoint2f(points);
                try { rectangle=Imgproc.minAreaRect(polygon); }
                finally { polygon.release(); }
                if (Math.min(rectangle.size.width,rectangle.size.height) < 5) continue;
                points=ordered(rectangle);
                for (Point point:points) {
                    point.x=(int)clamp(Math.rint(point.x/width*sourceWidth),0,sourceWidth-1);
                    point.y=(int)clamp(Math.rint(point.y/height*sourceHeight),0,sourceHeight-1);
                }
                if ((int)distance(points[0],points[1]) <=3 || (int)distance(points[0],points[3]) <=3) continue;
                boxes.add(points);
            }
            return boxes;
        } finally {
            for (Mat contour:contours) contour.release();
            probability.release();bitmap.release();kernel.release();hierarchy.release();
        }
    }

    private static double polygonScore(Mat probability, Point[] points) {
        int xmin=probability.cols()-1,ymin=probability.rows()-1,xmax=0,ymax=0;
        for (Point point:points) {
            xmin=Math.min(xmin,(int)clamp(Math.floor(point.x),0,probability.cols()-1));
            ymin=Math.min(ymin,(int)clamp(Math.floor(point.y),0,probability.rows()-1));
            xmax=Math.max(xmax,(int)clamp(Math.ceil(point.x),0,probability.cols()-1));
            ymax=Math.max(ymax,(int)clamp(Math.ceil(point.y),0,probability.rows()-1));
        }
        Mat mask=Mat.zeros(ymax-ymin+1,xmax-xmin+1,CvType.CV_8U);
        Point[] local=new Point[points.length];
        for(int index=0;index<points.length;index++) local[index]=new Point((int)(points[index].x-xmin),(int)(points[index].y-ymin));
        MatOfPoint polygon=new MatOfPoint(local);
        Mat roi=probability.submat(new Rect(xmin,ymin,xmax-xmin+1,ymax-ymin+1));
        try {
            Imgproc.fillPoly(mask,Collections.singletonList(polygon),new Scalar(1));
            return Core.mean(roi,mask).val[0];
        } finally {mask.release();polygon.release();roi.release();}
    }

    private static Point[] ordered(RotatedRect rectangle) {
        Point[] points=new Point[4];rectangle.points(points);
        Arrays.sort(points,Comparator.comparingDouble(point->point.x));
        Point tl=points[0].y<points[1].y?points[0]:points[1];
        Point bl=points[0].y<points[1].y?points[1]:points[0];
        Point tr=points[2].y<points[3].y?points[2]:points[3];
        Point br=points[2].y<points[3].y?points[3]:points[2];
        return new Point[]{tl,tr,br,bl};
    }

    private static void sortBoxes(List<Point[]> boxes) {
        boxes.sort(Comparator.<Point[]>comparingDouble(box->box[0].y).thenComparingDouble(box->box[0].x));
        for (int index=0;index<boxes.size()-1;index++) for(int j=index;j>=0;j--) {
            Point[] first=boxes.get(j),second=boxes.get(j+1);
            if(Math.abs(first[0].y-second[0].y)<10 && second[0].x<first[0].x) Collections.swap(boxes,j,j+1);
            else break;
        }
    }

    private static Mat crop(Mat source,Point[] points) {
        int width=(int)Math.max(distance(points[0],points[1]),distance(points[2],points[3]));
        int height=(int)Math.max(distance(points[0],points[3]),distance(points[1],points[2]));
        width=Math.max(width,1);height=Math.max(height,1);
        MatOfPoint2f from=new MatOfPoint2f(points);
        MatOfPoint2f to=new MatOfPoint2f(new Point(0,0),new Point(width,0),new Point(width,height),new Point(0,height));
        Mat transform=Imgproc.getPerspectiveTransform(from,to),result=new Mat();
        try {
            Imgproc.warpPerspective(source,result,transform,new Size(width,height),Imgproc.INTER_CUBIC,Core.BORDER_REPLICATE,new Scalar(0));
            if (height/(double)width>=1.5) Core.rotate(result,result,Core.ROTATE_90_COUNTERCLOCKWISE);
            return result;
        } catch(RuntimeException exception) {result.release();throw exception;}
        finally {from.release();to.release();transform.release();}
    }

    private boolean rotate180IfNeeded(Mat image) throws Exception {
        Mat resized=new Mat();
        try {
            int width=Math.min(192,(int)Math.ceil(48*image.cols()/(double)image.rows()));
            Imgproc.resize(image,resized,new Size(Math.max(1,width),48));
            try(OnnxTensor tensor=tensor(resized,192);
                OrtSession.Result result=classifier.run(Collections.singletonMap("x",tensor))) {
                float[] scores=((float[][])result.get(0).getValue())[0];
                boolean rotate=scores[1]>scores[0] && scores[1]>.9;
                if(rotate) Core.rotate(image,image,Core.ROTATE_180);
                return rotate;
            }
        } finally {resized.release();}
    }

    private OcrMath.Decoded recognizeLine(Mat image) throws Exception {
        int paddedWidth=OcrMath.recWidth(image.cols(),image.rows());
        if(paddedWidth>16384) throw new IllegalArgumentException("文字行过长，请分段识别");
        int width=Math.min(paddedWidth,(int)Math.ceil(48*image.cols()/(double)image.rows()));
        Mat resized=new Mat();
        try {
            Imgproc.resize(image,resized,new Size(Math.max(1,width),48));
            try(OnnxTensor tensor=tensor(resized,paddedWidth);
                OrtSession.Result result=recognizer.run(Collections.singletonMap("x",tensor))) {
                return OcrMath.decode(((float[][][])result.get(0).getValue())[0],dictionary);
            }
        } finally {resized.release();}
    }

    private OnnxTensor tensor(Mat image,int paddedWidth) throws Exception {
        byte[] pixels=new byte[(int)(image.total()*image.channels())];
        image.get(0,0,pixels);
        float[] normalized=OcrMath.normalizeBgr(pixels,image.rows(),image.cols(),paddedWidth);
        return OnnxTensor.createTensor(environment,FloatBuffer.wrap(normalized),new long[]{1,3,image.rows(),paddedWidth});
    }

    private static byte[] read(AssetSource source,String name) throws Exception {
        try(InputStream input=source.open(name);ByteArrayOutputStream output=new ByteArrayOutputStream()) {
            byte[] buffer=new byte[65536];int count;
            while((count=input.read(buffer))!=-1) output.write(buffer,0,count);
            return output.toByteArray();
        }
    }

    private static String sha256(byte[] bytes) throws Exception {
        byte[] digest=MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder result=new StringBuilder();
        for(byte value:digest) result.append(String.format(Locale.US,"%02x",value&255));
        return result.toString();
    }

    private static double clamp(double value,double minimum,double maximum) {return Math.max(minimum,Math.min(maximum,value));}
    private static double distance(Point first,Point second) {return Math.hypot(first.x-second.x,first.y-second.y);}
    private static double[][] toArray(Point[] points) {
        double[][] result=new double[points.length][2];
        for(int index=0;index<points.length;index++) {result[index][0]=points[index].x;result[index][1]=points[index].y;}
        return result;
    }

    @Override public synchronized void close() {
        if(closed) return;
        closed=true;
        for(OrtSession session:new OrtSession[]{detector,recognizer,classifier}) if(session!=null) {
            try {session.close();} catch(Exception ignored) { /* best-effort close after a failed initialization */ }
        }
        // OrtEnvironment is process-shared and is intentionally not owned here.
    }
}
