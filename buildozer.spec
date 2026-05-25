[app]
title = Sims 4 Translator
package.name = sims4translator
package.domain = com.alucardyummy
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,json,xml,pem
source.include_patterns = templates/*.html,packer/*.py,singletons/*.py,prefs/languages.xml,img/*.png
source.exclude_dirs = .github,__pycache__,.buildozer,bin,certs,java
version = 1.4
requirements = python3==3.10,kivy==2.2.1,flask,werkzeug,click,itsdangerous,jinja2,markupsafe,requests
p4a.branch = develop
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/img/icon-inicio.png
presplash.filename = %(source.dir)s/img/icon-inicio.png
presplash.color = #0f0f0f
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1
