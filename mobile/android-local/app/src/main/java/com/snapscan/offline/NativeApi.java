package com.snapscan.offline;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.ExifInterface;
import org.json.*;
import org.opencv.android.OpenCVLoader;
import org.opencv.android.Utils;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import org.opencv.imgcodecs.Imgcodecs;
import java.io.*;
import java.util.*;

/** In-process backend: no socket, remote address, or model download path. */
public final class NativeApi implements AutoCloseable {
    public static final class ApiException extends Exception {
        public final int statusCode;
        public ApiException(int statusCode,String message){super(message);this.statusCode=statusCode;}
    }
    private final Context context;
    private final LocalFiles files;
    private final DocumentStore store;
    private OcrEngine ocr;
    private boolean closed;
    public NativeApi(Context context)throws Exception {
        this.context=context.getApplicationContext();this.files=new LocalFiles(this.context);this.store=new DocumentStore(this.context);
        if(!OpenCVLoader.initLocal())throw new ApiException(503,"手机图像处理库加载失败");
    }
    public LocalFiles getFiles(){return files;}
    private OcrEngine engine()throws ApiException {
        if(ocr==null)try{ocr=new OcrEngine(context);}catch(Exception error){throw new ApiException(503,"本地OCR模型加载失败："+error.getMessage());}
        return ocr;
    }
    public synchronized JSONObject execute(String operation,JSONObject payload)throws Exception {
        if(closed)throw new ApiException(503,"本地引擎已关闭，请重新打开应用");
        try { switch(operation){
            case "health":return new JSONObject().put("status","ok").put("runtime","android-local").put("network_required",false).put("ocr",new JSONObject().put("ready",true).put("model",engine().modelInfo()));
            case "localFile":files.resolve(payload.getString("filePath"));return new JSONObject().put("ok",true);
            case "correct":return correct(payload);
            case "recognize":return recognize(payload);
            case "history":return new JSONObject().put("ok",true).put("method","sqlite_literal").put("items",store.list(payload.optString("q",""),payload.optInt("limit",100)));
            case "detail":return new JSONObject().put("ok",true).put("document",store.get(id(payload,"id")));
            case "saveDocument":return new JSONObject().put("ok",true).put("document",store.save(id(payload,"id"),payload.getJSONArray("texts"),revision(payload)));
            case "toPDF":return export(payload,true);
            case "toExcel":return export(payload,false);
            default:throw new ApiException(404,"不支持的本地操作");
        }}catch(JSONException|IllegalArgumentException error){throw new ApiException(422,"本地操作参数无效："+error.getMessage());}
    }
    private static long id(JSONObject payload,String key)throws Exception {Object value=payload.get(key);long id;if(value instanceof Number){double d=((Number)value).doubleValue();id=((Number)value).longValue();if(d!=id)throw new ApiException(422,"记录编号必须为整数");}else{try{id=Long.parseLong(String.valueOf(value));}catch(NumberFormatException error){throw new ApiException(422,"记录编号无效");}}if(id<=0)throw new ApiException(422,"记录编号无效");return id;}
    private static int revision(JSONObject payload)throws Exception {Object value=payload.opt("revision");if(!(value instanceof Number)||((Number)value).doubleValue()!=((Number)value).intValue()||((Number)value).intValue()<0)throw new ApiException(422,"必须提供有效修订号");return ((Number)value).intValue();}
    private JSONObject correct(JSONObject payload)throws Exception {
        long started=System.nanoTime();File source=files.resolve(payload.getString("filePath"));Mat bgr=readBgr(source);List<File> created=new ArrayList<>();
        try{
            JSONArray quad=payload.isNull("quad")?null:payload.optJSONArray("quad");if(payload.has("quad")&&!payload.isNull("quad")&&quad==null)throw new ApiException(422,"四角必须为坐标数组");
            try(ScanProcessor.Result scan=ScanProcessor.process(bgr,quad,payload.optString("mode","clean"))){
                File original=writeImage(created,"original","jpg",bgr,true);
                File cropped=writeImage(created,"cropped","jpg",scan.cropped,true);
                File enhanced=writeImage(created,"corrected","png",scan.enhanced,false);
                return new JSONObject().put("ok",true).put("has_quad",scan.hasQuad).put("quad",scan.hasQuad?scan.quad:JSONObject.NULL)
                    .put("original_image",files.path(original)).put("cropped_image",files.path(cropped)).put("image",files.path(enhanced))
                    .put("source_size",new JSONArray(new int[]{bgr.rows(),bgr.cols()})).put("size",new JSONArray(new int[]{scan.enhanced.rows(),scan.enhanced.cols()}))
                    .put("mode",payload.optString("mode","clean")).put("detection",scan.detection).put("quality",scan.quality).put("warnings",scan.warnings).put("elapsed_ms",(System.nanoTime()-started)/1_000_000);
            }
        }catch(Exception error){for(File file:created)file.delete();throw error;}finally{bgr.release();}
    }
    private File writeImage(List<File> created,String prefix,String ext,Mat image,boolean preview)throws Exception {
        File file=files.allocate(prefix,ext);created.add(file);Mat scaled=null;
        try{
            Mat use=image;if(preview&&Math.max(image.cols(),image.rows())>1600){double scale=1600.0/Math.max(image.cols(),image.rows());scaled=new Mat();Imgproc.resize(image,scaled,new Size(Math.round(image.cols()*scale),Math.round(image.rows()*scale)),0,0,Imgproc.INTER_AREA);use=scaled;}
            boolean ok;if(ext.equals("jpg")){MatOfInt params=new MatOfInt(Imgcodecs.IMWRITE_JPEG_QUALITY,95);try{ok=Imgcodecs.imwrite(file.getAbsolutePath(),use,params);}finally{params.release();}}else ok=Imgcodecs.imwrite(file.getAbsolutePath(),use);
            if(!ok||file.length()==0)throw new IOException("扫描图保存失败");return file;
        }finally{if(scaled!=null)scaled.release();}
    }
    private JSONObject recognize(JSONObject payload)throws Exception {
        long started=System.nanoTime();File source=files.resolve(payload.getString("filePath"));Mat bgr=readBgr(source);File saved=null;boolean committed=false;
        try{
            OcrEngine engine=engine();JSONObject result=engine.recognize(bgr);result.put("model",engine.modelInfo());
            saved=files.allocate("scan","png");
            // Persist the same normalized pixels used by OCR, including sampling and EXIF rotation.
            if(!Imgcodecs.imwrite(saved.getAbsolutePath(),bgr))throw new IOException("保存识别图失败");
            JSONObject document=store.insert(result,files.path(saved),bgr.cols(),bgr.rows(),source.getName(),(System.nanoTime()-started)/1_000_000);committed=true;
            for(String key:new String[]{"id","image","image_width","image_height","revision","elapsed_ms"})result.put(key,document.get(key));
            return result.put("ok",true);
        }finally{bgr.release();if(!committed&&saved!=null)saved.delete();}
    }
    private JSONObject export(JSONObject payload,boolean pdf)throws Exception {
        JSONObject document;
        if(payload.has("scan_id")&&!payload.isNull("scan_id")){
            document=store.get(id(payload,"scan_id"));if(document.getInt("revision")!=revision(payload))throw new ApiException(409,"文档已更新，请重新打开后导出");
        }else document=new JSONObject().put("text",payload.optString("text","")).put("boxes",payload.optJSONArray("boxes")==null?new JSONArray():payload.getJSONArray("boxes")).put("revision",JSONObject.NULL);
        String mode=payload.optString("mode",document.has("image")?"searchable":"text");
        if(pdf&&!mode.equals("searchable")&&!mode.equals("text"))throw new ApiException(422,"PDF模式无效");
        File image=null;if(pdf&&mode.equals("searchable")){if(!document.has("image"))throw new ApiException(422,"保留版式PDF需要已保存的扫描图");image=files.resolve(document.getString("image"));}
        File out=files.allocate("export",pdf?"pdf":"xlsx");boolean success=false;
        try{
            if(pdf)DocumentExporter.pdf(context,document,image,out,mode);else DocumentExporter.excel(document.getJSONArray("boxes"),out);
            success=true;return new JSONObject().put("ok",true).put("file",files.path(out)).put("mode",pdf?mode:"ocr_rows").put("revision",document.opt("revision"));
        }finally{if(!success)out.delete();}
    }
    /** Bounds the decode before allocation and normalizes EXIF into the coordinate system. */
    public static Mat readBgr(File file)throws Exception {
        BitmapFactory.Options options=new BitmapFactory.Options();options.inJustDecodeBounds=true;BitmapFactory.decodeFile(file.getAbsolutePath(),options);
        if(options.outWidth<2||options.outHeight<2)throw new ApiException(422,"图片无法解码");
        options.inSampleSize=1;while(Math.max(options.outWidth,options.outHeight)/options.inSampleSize>4096)options.inSampleSize*=2;
        options.inJustDecodeBounds=false;options.inPreferredConfig=Bitmap.Config.ARGB_8888;
        Bitmap bitmap=BitmapFactory.decodeFile(file.getAbsolutePath(),options);if(bitmap==null)throw new ApiException(422,"图片无法解码");
        Mat rgba=new Mat(),bgr=new Mat();try{Utils.bitmapToMat(bitmap,rgba);Imgproc.cvtColor(rgba,bgr,Imgproc.COLOR_RGBA2BGR);}finally{bitmap.recycle();rgba.release();}
        int orientation=ExifInterface.ORIENTATION_NORMAL;try{orientation=new ExifInterface(file.getAbsolutePath()).getAttributeInt(ExifInterface.TAG_ORIENTATION,ExifInterface.ORIENTATION_NORMAL);}catch(IOException ignored){}
        switch(orientation){
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL:Core.flip(bgr,bgr,1);break;
            case ExifInterface.ORIENTATION_ROTATE_180:Core.rotate(bgr,bgr,Core.ROTATE_180);break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL:Core.flip(bgr,bgr,0);break;
            case ExifInterface.ORIENTATION_TRANSPOSE:Core.transpose(bgr,bgr);break;
            case ExifInterface.ORIENTATION_ROTATE_90:Core.rotate(bgr,bgr,Core.ROTATE_90_CLOCKWISE);break;
            case ExifInterface.ORIENTATION_TRANSVERSE:Core.transpose(bgr,bgr);Core.flip(bgr,bgr,-1);break;
            case ExifInterface.ORIENTATION_ROTATE_270:Core.rotate(bgr,bgr,Core.ROTATE_90_COUNTERCLOCKWISE);break;
        }return bgr;
    }
    @Override public synchronized void close(){if(closed)return;closed=true;if(ocr!=null)ocr.close();store.close();}
}
