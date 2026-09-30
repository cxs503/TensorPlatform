import subprocess, sys, os, json, signal

JH_PORT = int(os.environ.get('JH_PORT', '8888'))
JH_BASE = os.environ.get('JH_BASE', '/app/uploads/open-digital-foundation/backend/notebooks')

jhub = None

def start():
    global jhub
    if jhub:
        return {"status": "already_running", "port": JH_PORT}
    try:
        jhub = subprocess.Popen([
            sys.executable, '-m', 'jupyterhub',
            '--port', str(JH_PORT),
            '--no-ssl',
            f'--Spawner.notebook_dir={JH_BASE}',
            '--Spawner.default_url=/lab',
            '--authenticator_class=jupyterhub.auth.DummyAuthenticator',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "started", "port": JH_PORT}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def stop():
    global jhub
    if jhub:
        jhub.send_signal(signal.SIGTERM)
        jhub = None
        return {"status": "stopped"}
    return {"status": "not_running"}

def status():
    if jhub and jhub.poll() is None:
        return {"status": "running", "port": JH_PORT}
    return {"status": "not_running"}
