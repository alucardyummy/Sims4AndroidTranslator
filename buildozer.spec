[app]
title = Sims 4 Translator
package.name = sims4translator
package.domain = com.alucardyummy
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,json, pem
source.include_patterns = templates/*.html,packer/*.py,singletons/*.py,prefs/languages.xml,certs/cert.pem,certs/key.pem
version = 1.3
requirements = python3,kivy,flask,requests
orientation = portrait
fullscreen = 0
android.add_src = java/
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = 1
android.archs = arm64-v8a

[buildozer]
log_level = 2
