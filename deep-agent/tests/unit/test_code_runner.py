"""CodeRunner 单元测试"""
import pytest
import asyncio


@pytest.fixture
def event_loop():
    """为 async 测试创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_code_runner_success():
    """测试：代码执行成功"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    source_files = [
        {
            "path": "main.py",
            "content": "print('hello from code runner')\nresult = 1 + 1\nprint(f'1+1={result}')",
        }
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is True
    assert "hello from code runner" in result.stdout
    assert "1+1=2" in result.stdout
    assert result.error == ""
    assert result.execution_time > 0


@pytest.mark.asyncio
async def test_code_runner_syntax_error():
    """测试：代码有语法错误时执行失败"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    source_files = [
        {
            "path": "main.py",
            "content": "print('hello'\n",  # 语法错误：缺少右括号
        }
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is False
    assert result.error != ""


@pytest.mark.asyncio
async def test_code_runner_runtime_error():
    """测试：代码运行时错误"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    source_files = [
        {
            "path": "main.py",
            "content": "x = 1 / 0\nprint(x)",
        }
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is False
    assert "ZeroDivisionError" in result.stderr or "division by zero" in result.stderr.lower()


@pytest.mark.asyncio
async def test_code_runner_timeout():
    """测试：代码执行超时"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=1)
    source_files = [
        {
            "path": "main.py",
            "content": "import time; time.sleep(10)",
        }
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is False
    assert "Timeout" in result.error or "timed out" in result.stderr.lower()


@pytest.mark.asyncio
async def test_code_runner_multiple_files():
    """测试：多个源文件写入和执行"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    source_files = [
        {
            "path": "utils.py",
            "content": "def add(a, b):\n    return a + b\n",
        },
        {
            "path": "main.py",
            "content": "from utils import add\nprint('Result:', add(3, 5))",
        },
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is True
    assert "Result: 8" in result.stdout


@pytest.mark.asyncio
async def test_code_runner_custom_command():
    """测试：自定义执行命令"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    source_files = [
        {
            "path": "script.py",
            "content": "import sys\nprint('Args:', sys.argv)",
        }
    ]
    result = await runner.run_source_files(source_files, run_cmd="python3 script.py extra_arg")

    assert result.success is True
    assert "extra_arg" in result.stdout


@pytest.mark.asyncio
async def test_code_runner_empty_files():
    """测试：空文件列表"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10)
    result = await runner.run_source_files([])

    assert result.success is False
    assert "No source files" in result.error


@pytest.mark.asyncio
async def test_code_runner_self_healing_retries():
    """测试：self-healing 在执行失败后最多重试 max_retries 次"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=2, timeout_seconds=10)
    # 第一个文件有语法错误，第二个尝试修复但还是会失败（因为我们没有 mock LLM）
    source_files = [
        {
            "path": "main.py",
            "content": "print('hello'\n",  # 语法错误
        }
    ]
    # 不 mock LLM，所以 self-healing 无法修复
    result = await runner.run_with_self_healing(source_files, task_title="Test Task")

    # 由于 LLM 不可用或返回 None，self-healing不会修复
    # 最终结果应该是失败
    assert result.success is False


@pytest.mark.asyncio
async def test_code_runner_output_truncation():
    """测试：输出被正确截断"""
    from app.agents.experts import CodeRunner

    runner = CodeRunner(max_retries=0, timeout_seconds=10, max_output_chars=50)
    source_files = [
        {
            "path": "main.py",
            "content": "print('x' * 1000)",
        }
    ]
    result = await runner.run_source_files(source_files)

    assert result.success is True
    assert len(result.stdout) <= 50 + 5  # 允许一些误差