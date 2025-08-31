import socket
import threading
import time
import random
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import select
from collections import deque
import argparse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("load_balancer.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("LoadBalancer")

class LoadBalanceAlgorithm(Enum):
    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    RANDOM = "random"
    IP_HASH = "ip_hash"

@dataclass
class BackendServer:
    host: str
    port: int
    healthy: bool = False  # Start as unhealthy until first health check passes
    active_connections: int = 0
    response_time: float = 0.0
    last_checked: float = 0.0
    fail_count: int = 0  # Track consecutive failures

class LoadBalancer:
    def __init__(self, host: str, port: int, algorithm: LoadBalanceAlgorithm = LoadBalanceAlgorithm.ROUND_ROBIN):
        self.host = host
        self.port = port
        self.algorithm = algorithm
        self.backend_servers: List[BackendServer] = []
        self.health_check_interval = 5  # seconds (reduced from 10)
        self.running = False
        self.server_queue = deque()
        self.lock = threading.RLock()
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "health_checks": 0,
            "server_failures": 0
        }
        
    def add_server(self, host: str, port: int):
        with self.lock:
            # Check if server already exists
            for server in self.backend_servers:
                if server.host == host and server.port == port:
                    logger.warning(f"Server {host}:{port} already exists")
                    return
            
            new_server = BackendServer(host, port)
            self.backend_servers.append(new_server)
            self.server_queue.append(new_server)
            logger.info(f"Added server: {host}:{port}")
            logger.info(f"NOTE: Server will be marked healthy once it passes health check")
    
    def remove_server(self, host: str, port: int):
        with self.lock:
            for i, server in enumerate(self.backend_servers):
                if server.host == host and server.port == port:
                    # Close all connections to this server
                    server.healthy = False
                    # Wait for active connections to complete
                    timeout = 10  # 10 second timeout
                    while server.active_connections > 0 and timeout > 0:
                        time.sleep(0.1)
                        timeout -= 0.1
                    self.backend_servers.pop(i)
                    logger.info(f"Removed server: {host}:{port}")
                    return
            logger.warning(f"Server {host}:{port} not found")
    
    def health_check(self):
        logger.info("Health check thread started")
        while self.running:
            time.sleep(self.health_check_interval)
            
            with self.lock:
                for server in self.backend_servers:
                    try:
                        start_time = time.time()
                        # Try to connect to the server
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(2)  # 2 second timeout
                        result = sock.connect_ex((server.host, server.port))
                        sock.close()
                        
                        if result == 0:  # Connection successful
                            server.response_time = (time.time() - start_time) * 1000  # ms
                            server.last_checked = time.time()
                            server.fail_count = 0  # Reset failure count
                            
                            if not server.healthy:
                                server.healthy = True
                                logger.info(f"Server {server.host}:{server.port} is now HEALTHY")
                        else:
                            server.fail_count += 1
                            if server.healthy and server.fail_count >= 3:  # Only mark unhealthy after 3 consecutive failures
                                server.healthy = False
                                logger.warning(f"Server {server.host}:{server.port} is now UNHEALTHY (connection refused)")
                            
                    except socket.timeout:
                        server.fail_count += 1
                        if server.healthy and server.fail_count >= 3:
                            server.healthy = False
                            logger.warning(f"Server {server.host}:{server.port} is now UNHEALTHY (timeout)")
                    except Exception as e:
                        server.fail_count += 1
                        if server.healthy and server.fail_count >= 3:
                            server.healthy = False
                            logger.warning(f"Server {server.host}:{server.port} is now UNHEALTHY: {e}")
                    
                    self.stats["health_checks"] += 1
            
            # Log status periodically
            healthy_count = sum(1 for s in self.backend_servers if s.healthy)
            if healthy_count == 0 and len(self.backend_servers) > 0:
                logger.warning("No healthy servers available. Start backend servers with: python -m http.server <port>")
    
    def select_server(self, client_ip: str = None) -> Optional[BackendServer]:
        with self.lock:
            healthy_servers = [s for s in self.backend_servers if s.healthy]
            
            if not healthy_servers:
                return None
            
            if self.algorithm == LoadBalanceAlgorithm.ROUND_ROBIN:
                # Rebuild queue with only healthy servers
                healthy_queue = [s for s in self.server_queue if s.healthy]
                if not healthy_queue:
                    healthy_queue = healthy_servers.copy()
                
                server = healthy_queue[0]
                healthy_queue = healthy_queue[1:] + [server]  # Rotate
                self.server_queue = deque(healthy_queue)
                return server
            
            elif self.algorithm == LoadBalanceAlgorithm.LEAST_CONNECTIONS:
                return min(healthy_servers, key=lambda s: s.active_connections)
            
            elif self.algorithm == LoadBalanceAlgorithm.RANDOM:
                return random.choice(healthy_servers)
            
            elif self.algorithm == LoadBalanceAlgorithm.IP_HASH and client_ip:
                # Simple IP-based hashing for sticky sessions
                hash_val = hash(client_ip) % len(healthy_servers)
                return healthy_servers[hash_val]
            
            # Default to round robin
            return healthy_servers[0]
    
    def forward_request(self, client_sock: socket.socket, client_addr: tuple):
        client_ip, client_port = client_addr
        logger.info(f"New connection from {client_ip}:{client_port}")
        
        # Select a backend server
        server = self.select_server(client_ip)
        if not server:
            error_msg = "HTTP/1.1 503 Service Unavailable\r\n\r\nNo healthy backend servers available"
            try:
                client_sock.send(error_msg.encode())
            except:
                pass
            client_sock.close()
            self.stats["failed_requests"] += 1
            logger.error("No healthy servers available to handle request")
            return
        
        server_sock = None
        try:
            # Connect to backend server
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.settimeout(10)
            server_sock.connect((server.host, server.port))
            
            # Increment connection count
            with self.lock:
                server.active_connections += 1
            
            # Forward data between client and server
            self._proxy_data(client_sock, server_sock, server)
            
            # Request successful
            self.stats["successful_requests"] += 1
            
        except Exception as e:
            logger.error(f"Error forwarding request to {server.host}:{server.port}: {e}")
            self.stats["failed_requests"] += 1
            self.stats["server_failures"] += 1
            
            # Mark server as unhealthy after a single failure during request
            with self.lock:
                server.healthy = False
                server.fail_count = 3  # Mark as definitely failed
            
        finally:
            # Clean up
            if server_sock:
                server_sock.close()
            client_sock.close()
            
            # Decrement connection count
            with self.lock:
                if server.active_connections > 0:
                    server.active_connections -= 1
    
    def _proxy_data(self, client_sock: socket.socket, server_sock: socket.socket, server: BackendServer):
        """Forward data between client and server sockets"""
        sockets = [client_sock, server_sock]
        
        while True:
            try:
                read_sockets, _, error_sockets = select.select(sockets, [], sockets, 1)
                
                if error_sockets:
                    break
                
                for sock in read_sockets:
                    data = sock.recv(4096)
                    if not data:
                        return
                    
                    if sock is client_sock:
                        # Data from client to server
                        server_sock.send(data)
                    else:
                        # Data from server to client
                        client_sock.send(data)
                        
            except (socket.timeout, socket.error):
                break
    
    def print_stats(self):
        with self.lock:
            print("\n" + "="*60)
            print("LOAD BALANCER STATISTICS")
            print("="*60)
            print(f"Algorithm: {self.algorithm.value}")
            print(f"Total Requests: {self.stats['total_requests']}")
            print(f"Successful: {self.stats['successful_requests']}")
            print(f"Failed: {self.stats['failed_requests']}")
            print(f"Health Checks: {self.stats['health_checks']}")
            print(f"Server Failures: {self.stats['server_failures']}")
            print("\nBACKEND SERVERS:")
            for i, server in enumerate(self.backend_servers):
                status = "HEALTHY" if server.healthy else "UNHEALTHY"
                print(f"  {i+1}. {server.host}:{server.port} [{status}]")
                print(f"     Connections: {server.active_connections}")
                print(f"     Response Time: {server.response_time:.2f}ms")
                print(f"     Fail Count: {server.fail_count}")
            print("="*60)
            if not any(s.healthy for s in self.backend_servers) and self.backend_servers:
                print("\nNOTE: No servers are healthy. Start backend servers with:")
                for server in self.backend_servers:
                    print(f"  python -m http.server {server.port}")
            print("="*60 + "\n")
    
    def start(self):
        self.running = True
        
        # Start health check thread
        health_thread = threading.Thread(target=self.health_check, daemon=True)
        health_thread.start()
        
        # Create load balancer socket
        lb_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lb_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            lb_socket.bind((self.host, self.port))
            lb_socket.listen(100)  # Allow up to 100 queued connections
            logger.info(f"Load balancer started on {self.host}:{self.port}")
            logger.info(f"Using algorithm: {self.algorithm.value}")
            
            # Initial health check
            time.sleep(1)
            
            while self.running:
                try:
                    client_sock, client_addr = lb_socket.accept()
                    self.stats["total_requests"] += 1
                    
                    # Handle client in a new thread
                    thread = threading.Thread(
                        target=self.forward_request,
                        args=(client_sock, client_addr),
                        daemon=True
                    )
                    thread.start()
                    
                except Exception as e:
                    if self.running:  # Only log if we're supposed to be running
                        logger.error(f"Error accepting connection: {e}")
        
        except Exception as e:
            logger.error(f"Failed to start load balancer: {e}")
        
        finally:
            lb_socket.close()
            self.running = False
    
    def stop(self):
        self.running = False
        logger.info("Load balancer stopped")

def main():
    parser = argparse.ArgumentParser(description="Python Load Balancer")
    parser.add_argument("--host", default="localhost", help="Load balancer host")
    parser.add_argument("--port", type=int, default=8080, help="Load balancer port")
    parser.add_argument("--algorithm", default="round_robin", 
                        choices=["round_robin", "least_connections", "random", "ip_hash"],
                        help="Load balancing algorithm")
    
    args = parser.parse_args()
    
    # Create load balancer
    algorithm = LoadBalanceAlgorithm(args.algorithm)
    lb = LoadBalancer(args.host, args.port, algorithm)
    
    # Add some example backend servers
    lb.add_server("localhost", 8001)
    lb.add_server("localhost", 8002)
    lb.add_server("localhost", 8003)
    
    print("="*60)
    print("PYTHON LOAD BALANCER")
    print("="*60)
    print("Backend servers added:")
    print("  - localhost:8001")
    print("  - localhost:8002") 
    print("  - localhost:8003")
    print("\nTo make servers healthy, run in separate terminals:")
    print("  python -m http.server 8001")
    print("  python -m http.server 8002")
    print("  python -m http.server 8003")
    print("="*60)
    
    # Start the load balancer in a separate thread
    lb_thread = threading.Thread(target=lb.start, daemon=True)
    lb_thread.start()
    
    # Command interface
    try:
        while True:
            command = input("\nEnter command (stats, add <host> <port>, remove <host> <port>, quit): ").strip().lower()
            
            if command == "stats":
                lb.print_stats()
            elif command.startswith("add"):
                parts = command.split()
                if len(parts) == 3:
                    try:
                        lb.add_server(parts[1], int(parts[2]))
                    except ValueError:
                        print("Invalid port number")
                else:
                    print("Usage: add <host> <port>")
            elif command.startswith("remove"):
                parts = command.split()
                if len(parts) == 3:
                    try:
                        lb.remove_server(parts[1], int(parts[2]))
                    except ValueError:
                        print("Invalid port number")
                else:
                    print("Usage: remove <host> <port>")
            elif command == "quit":
                break
            else:
                print("Unknown command")
    
    except KeyboardInterrupt:
        print("\nShutting down...")
    
    finally:
        lb.stop()

if __name__ == "__main__":
    main() 
