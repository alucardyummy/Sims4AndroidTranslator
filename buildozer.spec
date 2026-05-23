[app]
title = Sims 4 Translator
package.name = sims4translator
package.domain = com.alucardyummy
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,json
source.include_patterns = templates/*.html,packer/*.py,singletons/*.py,singletons/*.json,singletons/*.ini
version = 1.0
requirements = python3,kivy,flask,requests
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2

