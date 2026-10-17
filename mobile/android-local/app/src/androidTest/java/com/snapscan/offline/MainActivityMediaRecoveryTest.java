package com.snapscan.offline;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.webkit.WebView;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONObject;
import org.json.JSONTokener;
import org.junit.Test;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

/** System Activity callbacks are simulated; the real Activity/WebView/native chooser are exercised. */
public class MainActivityMediaRecoveryTest {
  @Test(timeout=60000) public void zeroByteSuccessfulCameraResultFailsAndRemovesOnlyItsEmptyCapture() throws Exception {
    Instrumentation instrumentation=InstrumentationRegistry.getInstrumentation();Context context=instrumentation.getTargetContext();
    Activity activity=instrumentation.startActivitySync(new Intent(context,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    LocalFiles files=new LocalFiles(context);File empty=files.allocate("source","jpg");assertEquals(0,empty.length());
    try {
      WebView view=find(activity.getWindow().getDecorView());waitFor(view,"document.body && document.body.innerText","纸面扫描",30000);
      evaluate(view,"window.__emptyCapture=null;var previous=window.__snapscanNativeResult;window.__snapscanNativeResult=function(id,e){if(id==='empty-capture')window.__emptyCapture=e;else if(previous)previous(id,e);};true");
      instrumentation.runOnMainSync(()->{set(activity,"mediaRequest","empty-capture");set(activity,"cameraOutput",empty);deliverResult(activity);});
      JSONObject failure=new JSONObject(waitFor(view,"JSON.stringify(window.__emptyCapture)","statusCode",10000));assertEquals(400,failure.getInt("statusCode"));
      assertFalse("The failed zero-byte capture must be removed",empty.exists());
    } finally {instrumentation.runOnMainSync(activity::finish);empty.delete();}
  }
  @Test(timeout=120000) public void recreationKeepsThePhotoAndReturnsItOnlyToANewConfirmedRequest() throws Exception {
    Instrumentation instrumentation=InstrumentationRegistry.getInstrumentation();
    Context context=instrumentation.getTargetContext();
    Activity activity=instrumentation.startActivitySync(new Intent(context,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    LocalFiles files=new LocalFiles(context); File photo=files.allocate("source","png");
    Bitmap bitmap=Bitmap.createBitmap(20,30,Bitmap.Config.ARGB_8888);
    try(FileOutputStream out=new FileOutputStream(photo)){assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG,100,out));}finally{bitmap.recycle();}
    try {
      Activity initial=activity;
      instrumentation.runOnMainSync(()->{set(initial,"mediaRequest","lost-old-page");set(initial,"cameraOutput",photo);});
      activity=recreate(instrumentation,activity);
      assertNull("A destroyed page request ID must never be restored",get(activity,"mediaRequest"));
      Activity returned=activity;
      instrumentation.runOnMainSync(()->deliverResult(returned));
      // A second recreation must retain an already recovered photo, too.
      activity=recreate(instrumentation,activity);
      WebView view=find(activity.getWindow().getDecorView()); assertNotNull(view);
      waitFor(view,"document.body && document.body.innerText","纸面扫描",30000);
      evaluate(view,"window.__recoveryResults={};var previous=window.__snapscanNativeResult;window.__snapscanNativeResult=function(id,e){if(id.indexOf('recovery-')===0||id==='lost-old-page')window.__recoveryResults[id]=e;else if(previous)previous(id,e);};window.SnapScanNative.request('recovery-cancel','chooseImage','{\"source\":\"album\"}');true");
      assertTrue("Recovered camera photo should offer confirmation instead of launching another picker",clickText(instrumentation,"继续这张照片",false));
      // Cancel must settle the new Promise and leave the recovered photo available.
      instrumentation.sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
      JSONObject cancelled=new JSONObject(waitFor(view,"JSON.stringify(window.__recoveryResults['recovery-cancel'])","statusCode",10000));
      assertEquals(499,cancelled.getInt("statusCode"));
      evaluate(view,"window.SnapScanNative.request('recovery-continue','chooseImage','{\"source\":\"camera\"}');true");
      assertTrue(clickText(instrumentation,"继续这张照片",true));
      JSONObject completed=new JSONObject(waitFor(view,"JSON.stringify(window.__recoveryResults['recovery-continue'])","filePath",10000));
      assertEquals(200,completed.getInt("statusCode"));
      assertEquals(files.url(photo),completed.getJSONObject("data").getString("filePath"));
      assertTrue(photo.isFile());
      assertEquals("false",evaluate(view,"Object.prototype.hasOwnProperty.call(window.__recoveryResults,'lost-old-page')"));
    } finally {Activity finalActivity=activity;instrumentation.runOnMainSync(finalActivity::finish);photo.delete();}
  }
  private static Activity recreate(Instrumentation instrumentation,Activity activity)throws Exception {
    Instrumentation.ActivityMonitor monitor=instrumentation.addMonitor(MainActivity.class.getName(),null,false);
    instrumentation.runOnMainSync(activity::recreate);
    Activity next=monitor.waitForActivityWithTimeout(15000);instrumentation.removeMonitor(monitor);
    assertNotNull("Activity should recreate",next);instrumentation.waitForIdleSync();return next;
  }
  private static void set(Activity activity,String name,Object value){try{Field field=MainActivity.class.getDeclaredField(name);field.setAccessible(true);field.set(activity,value);}catch(Exception error){throw new AssertionError(error);}}
  private static Object get(Activity activity,String name){try{Field field=MainActivity.class.getDeclaredField(name);field.setAccessible(true);return field.get(activity);}catch(Exception error){throw new AssertionError(error);}}
  private static void deliverResult(Activity activity){try{Method result=MainActivity.class.getDeclaredMethod("onActivityResult",int.class,int.class,Intent.class);result.setAccessible(true);result.invoke(activity,101,Activity.RESULT_OK,null);}catch(Exception error){throw new AssertionError(error);}}
  private static boolean clickText(Instrumentation instrumentation,String text,boolean click)throws Exception {
    long end=System.currentTimeMillis()+7000;
    while(System.currentTimeMillis()<end){AccessibilityNodeInfo root=instrumentation.getUiAutomation().getRootInActiveWindow();if(root!=null){List<AccessibilityNodeInfo> nodes=root.findAccessibilityNodeInfosByText(text);if(!nodes.isEmpty()){if(!click)return true;for(AccessibilityNodeInfo node:nodes){while(node!=null&&!node.isClickable())node=node.getParent();if(node!=null&&node.performAction(AccessibilityNodeInfo.ACTION_CLICK))return true;}}}Thread.sleep(150);}return false;
  }
  private static WebView find(View view){if(view instanceof WebView)return(WebView)view;if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++){WebView found=find(((ViewGroup)view).getChildAt(i));if(found!=null)return found;}return null;}
  private static String evaluate(WebView view,String script)throws Exception {AtomicReference<String> value=new AtomicReference<>();CountDownLatch done=new CountDownLatch(1);InstrumentationRegistry.getInstrumentation().runOnMainSync(()->view.evaluateJavascript(script,result->{value.set(result);done.countDown();}));assertTrue(done.await(5,TimeUnit.SECONDS));Object parsed=new JSONTokener(value.get()==null?"null":value.get()).nextValue();return parsed==JSONObject.NULL?"null":parsed.toString();}
  private static String waitFor(WebView view,String script,String needle,long timeout)throws Exception{long end=System.currentTimeMillis()+timeout;String last="";while(System.currentTimeMillis()<end){last=evaluate(view,script);if(last.contains(needle))return last;Thread.sleep(150);}fail("Expected "+needle+"; actual "+last);return last;}
}
