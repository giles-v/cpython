import asyncio
import threading
import unittest
from threading import Thread
from unittest import TestCase

from test.support import threading_helper

threading_helper.requires_working_threading(module=True)


class MyException(Exception):
    pass


def tearDownModule():
    asyncio._set_event_loop_policy(None)


class TestFreeThreading:
    def test_all_tasks_race(self) -> None:
        async def main():
            loop = asyncio.get_running_loop()
            future = loop.create_future()

            async def coro():
                await future

            tasks = set()

            async with asyncio.TaskGroup() as tg:
                for _ in range(100):
                    tasks.add(tg.create_task(coro()))

                all_tasks = self.all_tasks(loop)
                self.assertEqual(len(all_tasks), 101)

                for task in all_tasks:
                    self.assertEqual(task.get_loop(), loop)
                    self.assertFalse(task.done())

                current = self.current_task()
                self.assertEqual(current.get_loop(), loop)
                self.assertSetEqual(all_tasks, tasks | {current})
                future.set_result(None)

        def runner():
            with asyncio.Runner() as runner:
                loop = runner.get_loop()
                loop.set_task_factory(self.factory)
                runner.run(main())

        threads = []

        for _ in range(10):
            thread = Thread(target=runner)
            threads.append(thread)

        with threading_helper.start_threads(threads):
            pass

    def test_all_tasks_different_thread(self) -> None:
        loop = None
        started = threading.Event()
        done = threading.Event() # used for main task not finishing early
        async def coro():
            await asyncio.Future()

        lock = threading.Lock()
        tasks = set()

        async def main():
            nonlocal tasks, loop
            loop = asyncio.get_running_loop()
            started.set()
            for i in range(1000):
                with lock:
                    asyncio.create_task(coro())
                    tasks = self.all_tasks(loop)
            done.wait()

        runner = threading.Thread(target=lambda: asyncio.run(main()))

        def check():
            started.wait()
            with lock:
                self.assertSetEqual(tasks & self.all_tasks(loop), tasks)

        threads = [threading.Thread(target=check) for _ in range(10)]
        runner.start()

        with threading_helper.start_threads(threads):
            pass

        done.set()
        runner.join()

    def test_run_coroutine_threadsafe(self) -> None:
        results = []

        def in_thread(loop: asyncio.AbstractEventLoop):
            coro = asyncio.sleep(0.1, result=42)
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            result = fut.result()
            self.assertEqual(result, 42)
            results.append(result)

        async def main():
            loop = asyncio.get_running_loop()
            async with asyncio.TaskGroup() as tg:
                for _ in range(10):
                    tg.create_task(asyncio.to_thread(in_thread, loop))
            self.assertEqual(results, [42] * 10)

        with asyncio.Runner() as r:
            loop = r.get_loop()
            loop.set_task_factory(self.factory)
            r.run(main())

    def test_run_coroutine_threadsafe_exception(self) -> None:
        async def coro():
            await asyncio.sleep(0)
            raise MyException("test")

        def in_thread(loop: asyncio.AbstractEventLoop):
            fut = asyncio.run_coroutine_threadsafe(coro(), loop)
            return fut.result()

        async def main():
            loop = asyncio.get_running_loop()
            tasks = []
            for _ in range(10):
                task = loop.create_task(asyncio.to_thread(in_thread, loop))
                tasks.append(task)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            self.assertEqual(len(results), 10)
            for result in results:
                self.assertIsInstance(result, MyException)
                self.assertEqual(str(result), "test")

        with asyncio.Runner() as r:
            loop = r.get_loop()
            loop.set_task_factory(self.factory)
            r.run(main())


class TestPyFreeThreading(TestFreeThreading, TestCase):
    all_tasks = staticmethod(asyncio.tasks._py_all_tasks)
    current_task = staticmethod(asyncio.tasks._py_current_task)

    def factory(self, loop, coro, **kwargs):
        return asyncio.tasks._PyTask(coro, loop=loop, **kwargs)


@unittest.skipUnless(hasattr(asyncio.tasks, "_c_all_tasks"), "requires _asyncio")
class TestCFreeThreading(TestFreeThreading, TestCase):
    all_tasks = staticmethod(getattr(asyncio.tasks, "_c_all_tasks", None))
    current_task = staticmethod(getattr(asyncio.tasks, "_c_current_task", None))

    def factory(self, loop, coro, **kwargs):
        return asyncio.tasks._CTask(coro, loop=loop, **kwargs)


class TestEagerPyFreeThreading(TestPyFreeThreading):
    def factory(self, loop, coro, eager_start=True, **kwargs):
        return asyncio.tasks._PyTask(coro, loop=loop, **kwargs, eager_start=eager_start)


@unittest.skipUnless(hasattr(asyncio.tasks, "_c_all_tasks"), "requires _asyncio")
class TestEagerCFreeThreading(TestCFreeThreading, TestCase):
    def factory(self, loop, coro, eager_start=True, **kwargs):
        return asyncio.tasks._CTask(coro, loop=loop, **kwargs, eager_start=eager_start)