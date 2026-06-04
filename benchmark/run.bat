@echo off
REM Run the Relora load test.
REM Ensures the stack is up with the benchmark rate-limit override before testing.

set ROOT=%~dp0..

echo Starting stack with benchmark override...
docker-compose -f "%ROOT%\docker-compose.yml" -f "%ROOT%\docker-compose.benchmark.yml" up -d --wait
if errorlevel 1 (
    echo ERROR: docker-compose failed to start the stack.
    exit /b 1
)

echo Stack healthy. Running benchmark...
python "%~dp0loadtest.py" %*
