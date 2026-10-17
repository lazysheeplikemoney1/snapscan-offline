# Android 明文HTTP（评审意见11）

- 现象：HBuilderX真机调试能跑，但打出的APK访问 http://192.168.137.1:8000 失败。
- 原因：Android API28+ 默认禁止 cleartext HTTP（官方Network Security规定）。
- 课程Demo允许局域网HTTP，但必须显式放行，且报告注明"实验环境，非生产安全方案"。
- 修法（二选一）：
  1. HBuilderX manifest.json -> app-plus -> distribute -> android -> usesCleartextTraffic=true
     （HBuilderX可视化：manifest -> App常用其它设置 -> 明文HTTP允许）
  2. 自定义 networkSecurityConfig 仅放行 192.168.137.1（更严谨，答辩可讲）。
- 验收：必须用"真正APK + 真正手机 + 局域网IP"走完全流程，不能只测调试基座。
