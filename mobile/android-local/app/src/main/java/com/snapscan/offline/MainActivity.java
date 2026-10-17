package com.snapscan.offline;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLConnection;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Offline-only Activity: trusted packaged HTML + asynchronous local operations, no TCP server. */
public class MainActivity extends Activity {
    private static final int CAMERA = 101, ALBUM = 102, CAMERA_PERMISSION = 201, SAVE_PERMISSION = 202;
    private final ExecutorService worker = Executors.newSingleThreadExecutor(runnable -> new Thread(() -> {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND); runnable.run();
    }, "snapscan-local"));
    private WebView webView;
    private NativeApi api;
    private String mediaRequest;
    private File cameraOutput;
    private File recoveredCapture;
    private String saveRequest;
    private JSONObject savePayload;
    private volatile boolean destroyed;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try { api = new NativeApi(this); }
        catch (Exception error) {
            android.widget.TextView failure = new android.widget.TextView(this);
            failure.setText("本机组件初始化失败，请重新启动或检查安装包：" + error.getMessage());
            failure.setTextSize(18); failure.setPadding(28, 56, 28, 28); setContentView(failure); return;
        }
        if (state != null) {
            cameraOutput = restoredFile(state.getString("pending_camera"));
            recoveredCapture = restoredFile(state.getString("recovered_camera"));
        }
        getWindow().setStatusBarColor(Color.rgb(242, 239, 231));
        getWindow().setNavigationBarColor(Color.rgb(242, 239, 231));
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(242, 239, 231));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                Insets keyboard = insets.getInsets(WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard.bottom));
            } else view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        root.requestApplyInsets();
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setSafeBrowsingEnabled(true);
        webView.setBackgroundColor(Color.rgb(242, 239, 231));
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(PermissionRequest request) { request.deny(); }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return resource(request.getUrl().toString());
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return LocalUrlPolicy.appPath(request.getUrl().toString()) == null;
            }
        });
        webView.addJavascriptInterface(new Bridge(), "SnapScanNative");
        webView.loadUrl(LocalUrlPolicy.ORIGIN + "/index.html");
        if (Build.VERSION.SDK_INT >= 33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
    }

    private WebResourceResponse resource(String url) {
        String path = LocalUrlPolicy.appPath(url);
        if (path == null) return response(403, "Forbidden", "text/plain", new ByteArrayInputStream(new byte[0]));
        try {
            InputStream input;
            if (path.startsWith("/uploads/")) {
                File file = api.getFiles().resolve(url);
                if (!file.isFile()) throw new java.io.FileNotFoundException();
                input = new FileInputStream(file);
            } else {
                if (path.equals("/")) path = "/index.html";
                input = getAssets().open("www" + path);
            }
            return response(200, "OK", mime(path), input);
        } catch (Exception error) {
            return response(404, "Not Found", "text/plain", new ByteArrayInputStream("Local resource unavailable".getBytes(StandardCharsets.UTF_8)));
        }
    }
    private WebResourceResponse response(int code, String reason, String type, InputStream input) {
        Map<String, String> headers = new HashMap<>();
        headers.put("Cache-Control", "no-store");
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("Content-Security-Policy", "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'self'");
        return new WebResourceResponse(type, type.startsWith("text/") || type.contains("javascript") || type.contains("json") ? "UTF-8" : null, code, reason, headers, input);
    }
    private static String mime(String path) {
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".svg")) return "image/svg+xml";
        if (path.endsWith(".woff2")) return "font/woff2";
        if (path.endsWith(".ttf")) return "font/ttf";
        if (path.endsWith(".xlsx")) return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
        String guessed = URLConnection.guessContentTypeFromName(path);
        return guessed == null ? "application/octet-stream" : guessed;
    }

    private final class Bridge {
        @JavascriptInterface public void request(String id, String operation, String payloadJson) {
            if (destroyed || id == null || id.length() > 160 || operation == null) return;
            final JSONObject payload;
            try {
                if (payloadJson != null && payloadJson.length() > 2_000_000) throw new IllegalArgumentException();
                payload = new JSONObject(payloadJson == null || payloadJson.isEmpty() ? "{}" : payloadJson);
            } catch (Exception error) { fail(id, 400, "本机调用参数无效"); return; }
            if (operation.equals("chooseImage") || operation.equals("openFile") || operation.equals("shareFile") || operation.equals("saveImage")) {
                runOnUiThread(() -> mediaOperation(id, operation, payload)); return;
            }
            worker.execute(() -> {
                try { complete(id, 200, api.execute(operation, payload)); }
                catch (NativeApi.ApiException error) { fail(id, error.statusCode, error.getMessage()); }
                catch (Throwable error) { fail(id, 500, "本机处理失败：" + error.getClass().getSimpleName()); }
            });
        }
    }
    private void complete(String id, int status, JSONObject data) {
        try {
            JSONObject envelope = new JSONObject().put("statusCode", status).put("data", data);
            runOnUiThread(() -> { if (!destroyed && webView != null) webView.evaluateJavascript("window.__snapscanNativeResult && window.__snapscanNativeResult(" + JSONObject.quote(id) + "," + envelope + ");", null); });
        } catch (Exception ignored) { }
    }
    private void fail(String id, int code, String text) {
        try { complete(id, code, new JSONObject().put("ok", false).put("msg", text == null ? "本机操作失败" : text)); }
        catch (Exception ignored) { }
    }
    private void ok(String id) { try { complete(id, 200, new JSONObject().put("ok", true)); } catch (Exception ignored) { } }

    private void mediaOperation(String id, String operation, JSONObject payload) {
        try {
            if (operation.equals("chooseImage")) {
                if (mediaRequest != null || saveRequest != null) { fail(id, 409, "另一个系统选择操作尚未完成"); return; }
                mediaRequest = id;
                if (recoveredCapture != null && recoveredCapture.isFile() && recoveredCapture.length() > 0) {
                    new AlertDialog.Builder(this).setTitle("上次拍摄的照片已恢复")
                        .setMessage("系统曾重新打开扫描页面。可以继续这张照片，先检查边界和效果，再确认提取文字；也可以重新选择。")
                        .setPositiveButton("继续这张照片", (dialog, which) -> {
                            File recovered = recoveredCapture; recoveredCapture = null; mediaRequest = null;
                            try { complete(id, 200, new JSONObject().put("ok", true).put("filePath", api.getFiles().url(api.getFiles().resolve(api.getFiles().url(recovered))))); }
                            catch (Exception error) { fail(id, 400, "恢复的照片不可读取，请重新选择"); }
                        })
                        .setNegativeButton("重新选择", (dialog, which) -> { recoveredCapture = null; launchSelection(payload); })
                        .setOnCancelListener(dialog -> cancelMedia(499, "chooseImage:fail cancel")).show();
                } else { recoveredCapture = null; launchSelection(payload); }
            } else if (operation.equals("saveImage")) {
                if (Build.VERSION.SDK_INT < 29 && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                    if (saveRequest != null || mediaRequest != null) { fail(id, 409, "另一个系统选择操作尚未完成"); return; }
                    saveRequest = id; savePayload = payload;
                    requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, SAVE_PERMISSION);
                } else saveImage(id, payload);
            } else openFile(id, payload, operation.equals("shareFile"));
        } catch (ActivityNotFoundException error) { cancelMedia(501, "未找到可用的相机、相册或文件阅读应用"); if (!operation.equals("chooseImage")) fail(id, 501, "未找到可用的文件阅读应用"); }
        catch (Exception error) { if (operation.equals("chooseImage")) cancelMedia(500, "选择照片失败：" + error.getMessage()); else fail(id, 400, "本机文件操作失败：" + error.getMessage()); }
    }
    private void launchSelection(JSONObject payload) {
        try {
            if (payload.optString("source", "album").equals("camera")) {
                if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION);
                else launchCamera();
            } else startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/*"), ALBUM);
        } catch (ActivityNotFoundException error) { cancelMedia(501, "未找到可用的相机或相册应用"); }
        catch (Exception error) { cancelMedia(500, "选择照片失败：" + error.getMessage()); }
    }
    private void launchCamera() throws Exception {
        cameraOutput = api.getFiles().allocate("source", "jpg");
        Uri uri = FileProvider.getUriForFile(this, getPackageName() + ".localfiles", cameraOutput);
        Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE).putExtra(MediaStore.EXTRA_OUTPUT, uri);
        camera.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        camera.setClipData(ClipData.newRawUri("SnapScan capture", uri));
        startActivityForResult(camera, CAMERA);
    }
    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == CAMERA_PERMISSION) {
            if (mediaRequest == null) { recoveryNotice("页面已重新打开，请再次点击拍照。已授权的相机权限会保留。"); return; }
            if (granted) { try { launchCamera(); } catch (Exception error) { cancelMedia(500, "无法启动系统相机：" + error.getMessage()); } }
            else cancelMedia(403, "拍照权限被拒绝，请在系统设置允许相机权限");
        } else if (requestCode == SAVE_PERMISSION && saveRequest != null) {
            String id = saveRequest; JSONObject payload = savePayload; saveRequest = null; savePayload = null;
            if (granted) saveImage(id, payload); else fail(id, 403, "相册保存权限被拒绝，请在系统设置中允许保存照片");
        }
    }
    @Override public void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != CAMERA && requestCode != ALBUM) return;
        if (mediaRequest == null) {
            if (requestCode == CAMERA && resultCode == RESULT_OK && cameraOutput != null && cameraOutput.isFile() && cameraOutput.length() > 0) {
                recoveredCapture = cameraOutput; cameraOutput = null;
                recoveryNotice("刚拍摄的照片已保留。再次点击拍照或从相册选择，可以继续这张照片并检查扫描效果。");
            } else {
                if (cameraOutput != null && cameraOutput.length() == 0) cameraOutput.delete();
                cameraOutput = null;
                if (resultCode == RESULT_OK) recoveryNotice("扫描页面已重新打开，请再次选择照片。");
            }
            return;
        }
        if (resultCode != RESULT_OK) { cancelMedia(499, "chooseImage:fail cancel"); return; }
        String id = mediaRequest; mediaRequest = null;
        File captured = cameraOutput; cameraOutput = null;
        Uri selected = data == null ? null : data.getData();
        worker.execute(() -> {
            try {
                File file;
                if (requestCode == CAMERA) {
                    if (captured == null || !captured.isFile() || captured.length() == 0) throw new java.io.IOException("相机没有返回完整照片");
                    file = captured;
                } else {
                    if (selected == null) throw new java.io.IOException("相册没有返回照片");
                    try (InputStream input = getContentResolver().openInputStream(selected)) {
                        if (input == null) throw new java.io.IOException("照片不可读取");
                        file = api.getFiles().importImage(input, "selected.jpg");
                    }
                }
                complete(id, 200, new JSONObject().put("ok", true).put("filePath", api.getFiles().url(file)));
            } catch (Exception error) {
                if (requestCode == CAMERA && captured != null && captured.length() == 0) captured.delete();
                fail(id, 400, "读取照片失败：" + error.getMessage());
            }
        });
    }
    private void recoveryNotice(String message) {
        if (!destroyed) new AlertDialog.Builder(this).setTitle("照片选择已恢复").setMessage(message).setPositiveButton("知道了", null).show();
    }
    private File restoredFile(String url) {
        if (url == null) return null;
        try { return api.getFiles().resolve(url); } catch (Exception ignored) { return null; }
    }
    @Override public void onSaveInstanceState(Bundle state) {
        if (api != null) {
            try { if (cameraOutput != null && cameraOutput.isFile()) state.putString("pending_camera", api.getFiles().url(cameraOutput)); } catch (Exception ignored) { }
            try { if (recoveredCapture != null && recoveredCapture.isFile()) state.putString("recovered_camera", api.getFiles().url(recoveredCapture)); } catch (Exception ignored) { }
        }
        super.onSaveInstanceState(state);
    }
    private void cancelMedia(int code, String text) {
        String id = mediaRequest; mediaRequest = null;
        if (cameraOutput != null && cameraOutput.length() == 0) cameraOutput.delete();
        cameraOutput = null;
        if (id != null) fail(id, code, text);
    }
    private void saveImage(String id, JSONObject payload) {
        worker.execute(() -> {
            Uri destination = null;
            try {
                File file = api.getFiles().resolve(payload.optString("filePath"));
                String type = mime(file.getName());
                if (!file.isFile() || !type.startsWith("image/")) { fail(id, 415, "只能保存本机扫描图片"); return; }
                ContentValues values = new ContentValues();
                values.put(MediaStore.Images.Media.DISPLAY_NAME, "SnapScan_" + System.currentTimeMillis() + (type.equals("image/png") ? ".png" : ".jpg"));
                values.put(MediaStore.Images.Media.MIME_TYPE, type);
                if (Build.VERSION.SDK_INT >= 29) {
                    values.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/SnapScan");
                    values.put(MediaStore.Images.Media.IS_PENDING, 1);
                }
                destination = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
                if (destination == null) throw new java.io.IOException("系统相册不可写入");
                try (InputStream input = new FileInputStream(file); OutputStream output = getContentResolver().openOutputStream(destination)) {
                    if (output == null) throw new java.io.IOException("无法创建相册图片");
                    byte[] buffer = new byte[65536]; int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                }
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues ready = new ContentValues(); ready.put(MediaStore.Images.Media.IS_PENDING, 0);
                    getContentResolver().update(destination, ready, null, null);
                }
                ok(id);
            } catch (Exception error) {
                if (destination != null) try { getContentResolver().delete(destination, null, null); } catch (Exception ignored) { }
                fail(id, 500, "扫描图保存失败：" + error.getMessage());
            }
        });
    }
    private void openFile(String id, JSONObject payload, boolean share) throws Exception {
        File file = api.getFiles().resolve(payload.optString("filePath"));
        if (!file.isFile()) { fail(id, 404, "本机文件不存在"); return; }
        Uri uri = FileProvider.getUriForFile(this, getPackageName() + ".localfiles", file);
        Intent intent = new Intent(share ? Intent.ACTION_SEND : Intent.ACTION_VIEW);
        if (share) { intent.setType(mime(file.getName())); intent.putExtra(Intent.EXTRA_STREAM, uri); }
        else intent.setDataAndType(uri, mime(file.getName()));
        intent.setClipData(ClipData.newRawUri("SnapScan file", uri));
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        startActivity(Intent.createChooser(intent, share ? "分享本机文件" : "打开本机文件"));
        ok(id);
    }
    private void handleBack() { if (webView != null && webView.canGoBack()) webView.goBack(); else finish(); }
    @Override public void onBackPressed() { handleBack(); }
    @Override public void onDestroy() {
        destroyed = true;
        if (webView != null) { webView.removeJavascriptInterface("SnapScanNative"); webView.destroy(); webView = null; }
        if (api != null) worker.execute(() -> { try { api.close(); } catch (Exception ignored) { } });
        worker.shutdown();
        super.onDestroy();
    }
}
