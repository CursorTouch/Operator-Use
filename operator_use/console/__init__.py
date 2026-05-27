__all__ = ['cli']


def __getattr__(name: str):
    if name == 'cli':
        from operator_use.console.main import cli
        return cli
    raise AttributeError(name)
