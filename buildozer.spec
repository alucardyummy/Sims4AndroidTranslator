[app]
title = Sims 4 Translator
package.name = sims4translator
package.domain = com.alucardyummy
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,json
source.include_patterns = templates/*.html,packer/*.py,singletons/*.py,singletons/*.json
version = 1.2
requirements = python3,kivy,flask,requests
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = 1
android.archs = arm64-v8a
android.logcat_filters = *:S python:D
android.res = res/
android.manifest.application_attributes = android:usesCleartextTraffic="true" android:networkSecurityConfig="@xml/network_security_config"
p4a.hook = hook.py

[buildozer]
log_level = 2
