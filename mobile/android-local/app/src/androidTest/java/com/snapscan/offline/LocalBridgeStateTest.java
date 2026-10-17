package com.snapscan.offline;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
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

/** Complements MainActivityOfflineTest: actual H5 state and actual bridge errors.
 * No outside Activity, camera UI, permission change, or user content is accessed.
 */
public final class LocalBridgeStateTest {
    @Test(timeout=90000) public void pageUsesNativeHealthAndHidesComputerSettingsAndBridgeRejectsBadRequests() throws Exception {
        Instrumentation instrumentation=InstrumentationRegistry.getInstrumentation();
        Context context=instrumentation.getTargetContext();
        PackageInfo info=context.getPackageManager().getPackageInfo(context.getPackageName(),PackageManager.GET_PERMISSIONS);
        if(info.requestedPermissions!=null)for(String permission:info.requestedPermissions)assertNotEquals("android.permission.INTERNET",permission);
        Activity activity=instrumentation.startActivitySync(new Intent(context,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        File created=null;
        try {
            WebView view=findWebView(activity.getWindow().getDecorView());assertNotNull(view);
            // Let the production page's api.health()/Promise callback run untouched.
            String body=waitFor(view,"document.body && document.body.innerText","手机本地模型已就绪（rec_head_v1）",40000);
            assertTrue(body.contains("手机本地引擎"));assertTrue(body.contains("识别已就绪"));assertTrue(body.contains("完全离线"));assertTrue(body.contains("飞行模式"));
            assertEquals("https://appassets.androidplatform.net",evaluate(view,"location.origin"));
            assertNoComputerSettings(view);
            assertEquals("true",evaluate(view,"var h=document.querySelector('.connection-head');if(h)h.click();!!h"));
            instrumentation.waitForIdleSync();
            assertNoComputerSettings(view);

            // Only observe responses to our own IDs; production callback stays live.
            // SnapScanNative itself and NativeApi are the real app implementations.
            evaluate(view,"window.__bridgeContract={};window.__bridgeOriginal=window.__snapscanNativeResult;window.__snapscanNativeResult=function(id,e){if(String(id).indexOf('contract-')===0){window.__bridgeContract[id]=e;return;}if(window.__bridgeOriginal)window.__bridgeOriginal(id,e);};true");
            JSONObject unsupported=request(view,"contract-unknown","not-a-supported-operation","{}");
            assertEquals(404,unsupported.getInt("statusCode"));assertFalse(unsupported.getJSONObject("data").getBoolean("ok"));
            JSONObject malformed=request(view,"contract-malformed","health","[invalid json");
            assertEquals(400,malformed.getInt("statusCode"));assertFalse(malformed.getJSONObject("data").getBoolean("ok"));
            JSONObject external=request(view,"contract-external","localFile",new JSONObject().put("filePath","https://example.invalid/private.png").toString());
            assertTrue("Foreign file must fail with a prompt callback",external.getInt("statusCode")>=400);
            assertFalse(external.getJSONObject("data").getBoolean("ok"));

            LocalFiles files=new LocalFiles(context);created=files.allocate("bridge","png");
            Bitmap bitmap=Bitmap.createBitmap(16,16,Bitmap.Config.ARGB_8888);
            try(FileOutputStream output=new FileOutputStream(created)){assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG,100,output));}finally{bitmap.recycle();}
            JSONObject local=request(view,"contract-local","localFile",new JSONObject().put("filePath",files.url(created)).toString());
            assertEquals(200,local.getInt("statusCode"));assertTrue(local.getJSONObject("data").getBoolean("ok"));
            evaluate(view,"window.__snapscanNativeResult=window.__bridgeOriginal;delete window.__bridgeOriginal;true");
        } finally {
            if(created!=null)created.delete();
            instrumentation.runOnMainSync(activity::finish);
        }
    }
    private static void assertNoComputerSettings(WebView view)throws Exception {
        String body=evaluate(view,"document.body.innerText");
        assertFalse(body.contains("局域网后端"));assertFalse(body.contains("保存地址"));assertFalse(body.contains("恢复默认"));assertFalse(body.contains("192.168.58.120"));
        assertEquals("0",evaluate(view,"document.querySelectorAll('input.address,input[placeholder*=\"电脑\"],input[placeholder*=\"192.168\"]').length"));
    }
    private static JSONObject request(WebView view,String id,String operation,String payload)throws Exception {
        evaluate(view,"window.SnapScanNative.request("+JSONObject.quote(id)+","+JSONObject.quote(operation)+","+JSONObject.quote(payload)+");true");
        String raw=waitFor(view,"JSON.stringify(window.__bridgeContract["+JSONObject.quote(id)+"])","statusCode",10000);
        return new JSONObject(raw);
    }
    private static WebView findWebView(View view){
        if(view instanceof WebView)return (WebView)view;
        if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++){WebView found=findWebView(((ViewGroup)view).getChildAt(i));if(found!=null)return found;}
        return null;
    }
    private static String evaluate(WebView view,String script)throws Exception {
        CountDownLatch latch=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>();
        InstrumentationRegistry.getInstrumentation().runOnMainSync(()->view.evaluateJavascript(script,result->{value.set(result);latch.countDown();}));
        assertTrue("JavaScript callback timed out",latch.await(5,TimeUnit.SECONDS));
        Object result=new JSONTokener(value.get()==null?"null":value.get()).nextValue();return result==JSONObject.NULL?"null":result.toString();
    }
    private static String waitFor(WebView view,String script,String expected,long timeout)throws Exception {
        long deadline=System.currentTimeMillis()+timeout;String last="";
        while(System.currentTimeMillis()<deadline){last=evaluate(view,script);if(last.contains(expected))return last;Thread.sleep(150);}
        fail("Real page/bridge did not reach "+expected+"; last="+last.substring(0,Math.min(200,last.length())));return last;
    }
}
