# ⚖️ Load Balancer  

A lightweight **TCP Load Balancer** written in Python that distributes incoming client requests across multiple backend servers using different load balancing algorithms.  

---

## ✅ Features  
- Multiple algorithms:  
  - **Round Robin**  
  - **Least Connections**  
  - **Random**  
  - **IP Hash (sticky sessions)**  
- Automatic **health checks** (marks servers healthy/unhealthy).  
- **Command-line interface** for runtime control.  
- Logs activity to `load_balancer.log`.  
- Real-time **proxying** between clients and backend servers.  

---

## 🚀 Getting Started  

### 1. Clone Repository  
```bash
git clone https://github.com/your-username/python-load-balancer.git
cd python-load-balancer

```

### 2. Run Backend Servers

In separate terminals, start backend HTTP servers (example on ports 8001, 8002, and 8003):

```bash
python -m http.server 8001
python -m http.server 8002
python -m http.server 8003
```

### 3. Run Load Balancer

Start the load balancer (default: localhost:8080 with round-robin algorithm):

```bash
python load_balancer.py --host localhost --port 8080 --algorithm round_robin
```

### 4. Test the Setup

Open a browser or run:

```bash
curl http://localhost:8080
```

### ⚡ Command Interface

While the load balancer is running, you can type commands directly in its terminal:

Show statistics

```bash
stats
```

add server
```bash
add <host> <port>
```

remove server
```bash
remove <host> <port>
```

quit
```bash
quit
```



### ⚙️ Command Line Options
| Option        | Default       | Description                                                         |
| ------------- | ------------- | ------------------------------------------------------------------- |
| `--host`      | `localhost`   | Load balancer host                                                  |
| `--port`      | `8080`        | Load balancer port                                                  |
| `--algorithm` | `round_robin` | Algorithm (`round_robin`, `least_connections`, `random`, `ip_hash`) |

