import subprocess
import os
import tempfile
import time
import logging
from config import RUN_TIMEOUT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("executor")

class ExecutionResult:
    def __init__(self, success=False, output="", error="", compiled=True, execution_time=0.0):
        self.success = success
        self.output = output
        self.error = error
        self.compiled = compiled
        self.execution_time = execution_time

def run_in_docker(code_files: dict, language: str, stdin_data: str = "", time_limit: int = RUN_TIMEOUT, memory_limit: str = "256m"):
    with tempfile.TemporaryDirectory() as temp_dir:
        # 1. Determine Main File and Write Code
        main_file = ""
        for filename, content in code_files.items():
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, 'w') as f:
                f.write(content)
            
            if language == "python" and filename == "app.py": main_file = "app.py"
            elif language == "java" and filename == "Main.java": main_file = "Main.java"
            elif language == "c" and filename == "main.c": main_file = "main.c"
            elif language == "cpp" and filename == "main.cpp": main_file = "main.cpp"
            elif language == "javascript" and filename == "index.js": main_file = "index.js"
            elif language == "csharp" and (filename == "Submission.cs" or filename == "Program.cs"): main_file = filename

        if not main_file:
             return ExecutionResult(error="Main file not found or unsupported language configuration.")

        commands = {
            "python": [[f"python3 {main_file}"]],
            "javascript": [[f"node {main_file}"]],
            "java": [[f"javac {main_file}"], [f"java -cp . Main"]],
            "c": [[f"gcc {main_file} -o main"], [f"./main"]],
            "cpp": [[f"g++ {main_file} -o main"], [f"./main"]],
            "csharp": [[f"mcs -out:main.exe {main_file}"], [f"mono main.exe"]]
        }

        if language not in commands:
            return ExecutionResult(error=f"Unsupported language: {language}")

        cmds = commands[language]
        
        try:
            start_time = time.time()
            
            if len(cmds) > 1:
                compile_cmd = cmds[0]
                compile_proc = subprocess.run(
                    compile_cmd, cwd=temp_dir, shell=True, capture_output=True, text=True, timeout=10
                )
                if compile_proc.returncode != 0:
                    return ExecutionResult(compiled=False, error=compile_proc.stderr)
                run_cmd = cmds[1]
            else:
                run_cmd = cmds[0]
                
            run_proc = subprocess.run(
                run_cmd,
                cwd=temp_dir,
                input=stdin_data,
                shell=True,
                capture_output=True,
                text=True,
                timeout=time_limit
            )
            
            duration = round(time.time() - start_time, 3)
            
            if run_proc.returncode != 0:
                # Distinguish between timeout (handled by exception usually) and runtime error
                return ExecutionResult(
                    success=False, 
                    error=run_proc.stderr or "Runtime Error", 
                    output=run_proc.stdout,
                    execution_time=duration
                )

            return ExecutionResult(
                success=True, 
                output=run_proc.stdout, 
                execution_time=duration
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False, error="Time Limit Exceeded")
        except Exception as e:
            return ExecutionResult(success=False, error=f"System Error: {str(e)}")

def simulate_deployment_validation(language):
    return True, "Validation Skipped"