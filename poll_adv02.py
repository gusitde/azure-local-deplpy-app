"""Poll adv02 (192.168.1.32) until it comes online after OS install."""
import socket, time

host = "192.168.1.32"
start = time.time()
print(f"Polling {host} for SSH (22) and WinRM (5985) every 30s...")
print(f"Started at {time.strftime('%H:%M:%S')}")

attempt = 0
while True:
    attempt += 1
    elapsed = int(time.time() - start)
    mins = elapsed // 60
    secs = elapsed % 60

    for port, name in [(5985, "WinRM"), (22, "SSH")]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((host, port))
            s.close()
            print(f"\n✅ {name} port {port} is OPEN on {host} after {mins}m {secs}s!")
            print(f"   OS installation complete. Host is online.")
            exit(0)
        except:
            pass

    print(f"  [{mins:02d}:{secs:02d}] Attempt {attempt} - not ready yet")
    time.sleep(30)
