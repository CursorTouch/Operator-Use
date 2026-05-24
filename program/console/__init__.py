__all__ = ['cli']


def __getattr__(name: str):
    if name == 'cli':
        from program.console.main import cli
        return cli
    raise AttributeError(name)
