def hook(service, source_dir, p4a):
    import os
    manifest = os.path.join(source_dir, 'AndroidManifest.xml')
    if not os.path.exists(manifest):
        return
    with open(manifest, 'r') as f:
        content = f.read()
    if 'usesCleartextTraffic' not in content:
        content = content.replace(
            '<application',
            '<application android:usesCleartextTraffic="true"',
            1
        )
        with open(manifest, 'w') as f:
            f.write(content)
        print("hook.py: usesCleartextTraffic injetado no manifest")
