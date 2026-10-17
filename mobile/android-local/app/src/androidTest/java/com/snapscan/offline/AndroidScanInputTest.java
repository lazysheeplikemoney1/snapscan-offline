package com.snapscan.offline;

import static org.junit.Assert.*;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.media.ExifInterface;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;
import java.io.*;

/** One input contract, separate from document-detection heuristics. */
public class AndroidScanInputTest {
    @Test public void channelsSamplingAndExifUseNormalizedBgrCoordinates()throws Exception {
        assertTrue(OpenCVLoader.initLocal());
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        File png=File.createTempFile("bgr-contract-",".png",context.getCacheDir());
        File jpeg=File.createTempFile("exif-contract-",".jpg",context.getCacheDir());
        try {
            Bitmap small=Bitmap.createBitmap(80,60,Bitmap.Config.ARGB_8888);small.eraseColor(Color.rgb(24,101,220));
            try(FileOutputStream output=new FileOutputStream(png)){assertTrue(small.compress(Bitmap.CompressFormat.PNG,100,output));}finally{small.recycle();}
            Mat bgr=NativeApi.readBgr(png);MatOfByte bytes=new MatOfByte(java.nio.file.Files.readAllBytes(png.toPath()));
            Mat direct=Imgcodecs.imdecode(bytes,Imgcodecs.IMREAD_COLOR);
            try {assertArrayEquals(new double[]{220,101,24},bgr.get(20,20),0);assertEquals(0,Core.norm(bgr,direct,Core.NORM_INF),0);}
            finally{bgr.release();direct.release();bytes.release();}
            Bitmap wide=Bitmap.createBitmap(5000,64,Bitmap.Config.ARGB_8888);wide.eraseColor(Color.rgb(220,40,20));
            Canvas canvas=new Canvas(wide);Paint paint=new Paint();paint.setColor(Color.rgb(20,40,220));canvas.drawRect(2500,0,5000,64,paint);
            try(FileOutputStream output=new FileOutputStream(jpeg)){assertTrue(wide.compress(Bitmap.CompressFormat.JPEG,100,output));}finally{wide.recycle();}
            ExifInterface exif=new ExifInterface(jpeg.getAbsolutePath());exif.setAttribute(ExifInterface.TAG_ORIENTATION,String.valueOf(ExifInterface.ORIENTATION_ROTATE_90));exif.saveAttributes();
            Mat rotated=NativeApi.readBgr(jpeg);
            try {assertEquals(2500,rotated.rows());assertEquals(32,rotated.cols());double[] top=rotated.get(100,15),bottom=rotated.get(2399,15);assertTrue(top[2]>top[0]+150);assertTrue(bottom[0]>bottom[2]+150);}
            finally{rotated.release();}
        }finally{png.delete();jpeg.delete();}
    }
}
