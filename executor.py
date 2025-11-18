import docker
import os
import io
import logging
import tarfile
import time
import random
from typing import Optional
from config import RUN_TIMEOUT, MEMORY_LIMIT, DOCKER_NETWORK_DISABLED, ENABLE_DEPLOYMENT_VALIDATION, MIN_SECURITY_SCORE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("executor")

class ExecutionResult:
    def __init__(self, success=False, output="", error="", compiled=True, execution_time=0.0, memory_used=0):
        self.success = success
        self.output = output
        self.error = error
        self.compiled = compiled
        self.execution_time = execution_time
        self.memory_used = memory_used
def get_docker_config(language: str) -> Optional[dict]:
    configs = {
        "java": {
            "image": "eclipse-temurin:17-jdk-jammy",
            "filename": "Main.java",
            "compile": "javac -d /app Main.java",
            "run": "java -cp .:/app -XX:MaxRAM=256m Main"
        },
        "python": {
            "image": "python:3.11-slim",
            "filename": "app.py",
            "compile": None,
            "run": "python -B -E -S app.py"
        },
        "c": {
            "image": "gcc:11",
            "filename": "main.c",
            "compile": "gcc -O2 -std=c11 -o /app/main main.c -lm",
            "run": "/app/main"
        },
        "cpp": {
            "image": "gcc:11",
            "filename": "main.cpp",
            "compile": "g++ -O2 -std=c++17 -o /app/main main.cpp -lm",
            "run": "/app/main"
        },
        "javascript": {
            "image": "node:18-slim",
            "filename": "index.js",
            "compile": None,
            "run": "node --max-old-space-size=256 index.js"
        },
         "csharp": {
            "image": "mcr.microsoft.com/dotnet/sdk:7.0",
            "filename": "Submission.cs",
            "compile": "dotnet new console -o /app --force >/dev/null && mv /app/Submission.cs /app/Program.cs && dotnet build /app -c Release -o /app/build",
            "run": "/app/build/app",
            "env": {"DOTNET_CLI_HOME": "/tmp", "XDG_DATA_HOME": "/tmp"}
        }
    }
    return configs.get(language)

def copy_to_container(container, src_content: str, dst_path: str):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w|') as tar:
        encoded_data = src_content.encode('utf-8')
        tarinfo = tarfile.TarInfo(name=os.path.basename(dst_path))
        tarinfo.size = len(encoded_data)
        tar.addfile(tarinfo, io.BytesIO(encoded_data))
    stream.seek(0)
    try:
        container.put_archive(path=os.path.dirname(dst_path), data=stream)
        logger.debug(f"Successfully copied file to container: {dst_path}")
    except Exception as e:
        logger.error(f"Failed to copy file to container {dst_path}: {e}")
        raise

def simulate_deployment_validation(language: str) -> tuple[bool, str]:
    """
    Simulates enterprise deployment checks like security scanning and complexity.
    This simulates a CI/CD pipeline stage.
    """
    if not ENABLE_DEPLOYMENT_VALIDATION:
        return True, "Deployment validation skipped by configuration."

    logger.info(f"Starting deployment validation for {language}...")
    
    time.sleep(random.uniform(0.1, 0.5)) # Simulate processing time

    # Simulate random failure scenarios
    random_fail = random.random()
    
    if random_fail < 0.05: # 5% chance of a critical dependency failure
        return False, "Critical dependency failed during security audit (Vulnerability CVE-2025-1337 detected)."

    # Simulate security score check
    security_score = random.randint(MIN_SECURITY_SCORE - 10, 100)
    
    if security_score < MIN_SECURITY_SCORE:
        return False, f"Code failed static analysis (Security Score: {security_score}/{MIN_SECURITY_SCORE}). Check for excessive complexity or unsafe functions."

    logger.info("Deployment validation passed all checks.")
    return True, "Deployment validation successful."

def run_in_docker(code_files: dict, language: str, stdin_data: str = "", time_limit: int = RUN_TIMEOUT, memory_limit: str = MEMORY_LIMIT) -> ExecutionResult:
    config = get_docker_config(language)
    if not config:
        return ExecutionResult(error=f"Unsupported language: {language}")

    client = None
    container = None
    
    container_sleep_time = time_limit + 10 

    try:
        client = docker.from_env(timeout=30)
        
        container = client.containers.run(
            image=config["image"],
            command=f"sleep {container_sleep_time}",
            detach=True,
            network_mode="none" if DOCKER_NETWORK_DISABLED else "bridge",
            mem_limit=memory_limit,
            pids_limit=100,
            read_only=False,
            environment=config.get("env", {}),
            working_dir="/app",
            user="root"
        )
        logger.info(f"Started container: {container.short_id}")

        container.exec_run("mkdir -p /app", workdir="/", user="root")

        target_filename = config["filename"]
        main_code = code_files.get(target_filename) or list(code_files.values())[0]

        copy_to_container(container, main_code, f"/app/{target_filename}")
        
        if stdin_data:
            copy_to_container(container, stdin_data, "/app/stdin.txt")

        # --- Compilation Phase ---
        if config["compile"]:
            logger.debug(f"Compiling {target_filename} for {container.short_id}...")
            compile_res = container.exec_run(
                f"sh -c '{config['compile']}'", 
                workdir="/app",
                user="root"
            )
            
            if compile_res.exit_code != 0:
                logger.warning(f"Compilation failed for {container.short_id}.")
                return ExecutionResult(
                    compiled=False, 
                    error=compile_res.output.decode('utf-8', errors='ignore')
                )
            logger.debug("Compilation successful.")

        # --- Execution Phase ---
        run_cmd = config["run"]
        
        if language in ['c', 'cpp']:
            container.exec_run("chmod +x /app/main", workdir="/app", user="root")

        if stdin_data:
            full_cmd = f"timeout {time_limit} {run_cmd} < /app/stdin.txt"
        else:
            full_cmd = f"timeout {time_limit} {run_cmd}"

        logger.debug(f"Running command: {full_cmd} in {container.short_id}")

        exec_res = container.exec_run(
            f"sh -c '{full_cmd}'",
            workdir="/app",
            demux=True,
            user="root"
        )
        
        stdout_bytes, stderr_bytes = exec_res.output
        stdout = stdout_bytes.decode('utf-8', errors='ignore') if stdout_bytes else ""
        stderr = stderr_bytes.decode('utf-8', errors='ignore') if stderr_bytes else ""
        
        if exec_res.exit_code != 0:
            error_msg = stderr if stderr else f"Runtime Error (Exit Code {exec_res.exit_code})"
            
            if exec_res.exit_code == 124: 
                error_msg = "Time Limit Exceeded"
                logger.warning(f"Execution failed (TLE) for {container.short_id}")
            elif exec_res.exit_code == 137: 
                error_msg = "Memory Limit Exceeded"
                logger.warning(f"Execution failed (MLE) for {container.short_id}")
            else:
                logger.warning(f"Execution failed (RE) for {container.short_id} with exit code {exec_res.exit_code}")
            
            return ExecutionResult(
                success=False, 
                error=error_msg,
                output=stdout
            )
        
        return ExecutionResult(success=True, output=stdout)

    except docker.errors.ImageNotFound:
         logger.error(f"System Error: Docker image {config['image']} not found.")
         return ExecutionResult(error=f"System Error: Docker image {config['image']} not found. Check environment setup.")
    except Exception as e:
        logger.error(f"Critical System Error during Docker execution: {e}", exc_info=True)
        return ExecutionResult(error=f"Internal Error (IE): Execution failed due to a critical system error. Please try again.")
    finally:
        if container:
            try:
                container.remove(force=True)
                logger.info(f"Removed container: {container.short_id}")
            except Exception as e:
                logger.error(f"Failed to remove container {container.short_id}: {e}")