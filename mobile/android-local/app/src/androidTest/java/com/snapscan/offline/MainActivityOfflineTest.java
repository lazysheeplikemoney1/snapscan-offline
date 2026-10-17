package com.snapscan.offline;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONObject;
import org.json.JSONTokener;
import org.junit.Test;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

/** Exercises the actual packaged H5 page and Java bridge, with no HTTP server. */
public class MainActivityOfflineTest {
    @Test(timeout=90000) public void packagedWebViewRendersAndTheBridgeLoadsTheRealLocalModel() throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        Context context = instrumentation.getTargetContext();
        assertNotEquals(android.content.pm.PackageManager.PERMISSION_GRANTED, context.checkSelfPermission("android.permission.INTERNET"));
        Activity activity = instrumentation.startActivitySync(new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try {
            WebView view = findWebView(activity.getWindow().getDecorView());
            assertNotNull("Actual Activity must contain a WebView", view);
            waitFor(view, "document.body && document.body.innerText", "纸面扫描", 30000);
            assertEquals("true", evaluate(view, "typeof window.SnapScanNative.request === 'function'"));
            evaluate(view, "window.__uiSmoke=null;window.__previousNativeResult=window.__snapscanNativeResult;window.__snapscanNativeResult=function(id,e){if(id==='ui-smoke'){window.__uiSmoke=e;return;}if(window.__previousNativeResult)window.__previousNativeResult(id,e);};window.SnapScanNative.request('ui-smoke','health','{}');true");
            String health = waitFor(view, "JSON.stringify(window.__uiSmoke)", "rec_head_v1", 30000);
            JSONObject envelope = new JSONObject(health);
            assertEquals(200, envelope.getInt("statusCode"));
            assertTrue(envelope.getJSONObject("data").getJSONObject("ocr").getBoolean("ready"));
            assertEquals("rec_head_v1", envelope.getJSONObject("data").getJSONObject("ocr").getJSONObject("model").getString("id"));
            LocalFiles files = new LocalFiles(context);
            File image = files.allocate("source", "png");
            try {
                Bitmap bitmap = Bitmap.createBitmap(16, 16, Bitmap.Config.ARGB_8888);
                try (FileOutputStream output = new FileOutputStream(image)) { bitmap.compress(Bitmap.CompressFormat.PNG, 100, output); }
                bitmap.recycle();
                String script = "window.__imageSmoke='waiting';var i=new Image();i.onload=function(){window.__imageSmoke=i.naturalWidth+'x'+i.naturalHeight;};i.onerror=function(){window.__imageSmoke='failed';};i.src=" + JSONObject.quote(files.url(image)) + ";document.body.appendChild(i);true";
                evaluate(view, script);
                assertEquals("16x16", waitFor(view, "window.__imageSmoke", "16x16", 10000));
            } finally { image.delete(); }
        } finally { instrumentation.runOnMainSync(activity::finish); }
    }
    private static WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView)view;
        if (view instanceof ViewGroup) for (int i=0;i<((ViewGroup)view).getChildCount();i++) {
            WebView found = findWebView(((ViewGroup)view).getChildAt(i)); if (found != null) return found;
        }
        return null;
    }
    private static String evaluate(WebView view, String script) throws Exception {
        AtomicReference<String> value = new AtomicReference<>(); CountDownLatch latch = new CountDownLatch(1);
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> view.evaluateJavascript(script, result -> {value.set(result); latch.countDown();}));
        assertTrue("JavaScript callback timed out", latch.await(5,TimeUnit.SECONDS));
        Object result = new JSONTokener(value.get() == null ? "null" : value.get()).nextValue();
        return result == JSONObject.NULL ? "null" : result.toString();
    }
    private static String waitFor(WebView view, String script, String needle, long timeout) throws Exception {
        long deadline = System.currentTimeMillis() + timeout; String last = "";
        while (System.currentTimeMillis() < deadline) { last=evaluate(view,script); if(last.contains(needle))return last; Thread.sleep(200); }
        fail("Packaged WebView did not reach expected local state: " + needle + "; last=" + last.substring(0,Math.min(180,last.length()))); return last;
    }
}
