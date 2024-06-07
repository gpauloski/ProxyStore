"""ProxyStore mypy plugin.

The [`Proxy`][proxystore.proxy.Proxy] class behaves poorly with
[mypy](https://mypy-lang.org/) out of the box. Consider the following example.
Mypy can determine that `proxy` is of type `Proxy[Foo]` but is unable to
determine the correct types when accessing an attribute of `Foo` indirectly
via the [`Proxy`][proxystore.proxy.Proxy] instance.

```python linenums="1"
from proxystore.proxy import Proxy

class Foo:
    def bar(self) -> int:
        return 42

def factory() -> Foo:
    return Foo()

proxy = Proxy(factory)
reveal_type(proxy)  # Revealed type is "Proxy[Foo]"

bar = proxy.bar()
reveal_type(bar)  # Revealed type is "Any"
```

ProxyStore (v0.6.5 and later) comes with an optional mypy plugin which can fix
these type resolution limitations. With the mypy plugin enabled, we get the
correct type.

```python linenums="1"
proxy = Proxy(factory)
reveal_type(proxy)  # Revealed type is "Proxy[Foo]"

bar = proxy.bar()
reveal_type(bar)  # Revealed type is "int"
```

Enable the plugin by adding `proxystore.mypy_plugin` to the list of plugins
in your
[mypy config file](https://mypy.readthedocs.io/en/latest/config_file.html){target=_blank}.

* `pyproject.toml`
  ```toml
  [tools.mypy]
  plugins = ["proxystore.mypy_plugin"]
  ```
* `mypy.ini` and `setup.cfg`
  ```ini
  [mypy]
  plugins = proxystore.mypy_plugin
  ```
"""

from __future__ import annotations

import functools
import sys
from typing import Callable
from typing import TypeVar

if sys.version_info >= (3, 10):  # pragma: >=3.10 cover
    from typing import ParamSpec
else:  # pragma: <3.10 cover
    from typing_extensions import ParamSpec

from mypy.checkmember import analyze_member_access
from mypy.options import Options
from mypy.plugin import AttributeContext
from mypy.plugin import Plugin
from mypy.types import AnyType
from mypy.types import get_proper_type
from mypy.types import Instance
from mypy.types import Type
from mypy.types import TypeOfAny
from mypy.types import TypeVarType
from mypy.types import UnionType

P = ParamSpec('P')
T = TypeVar('T')

PROXY_FULLNAME = 'proxystore.proxy.Proxy'


class ProxyStoreMypyPlugin(Plugin):  # noqa: D101
    def __init__(self, options: Options) -> None:
        super().__init__(options)

    def get_attribute_hook(  # noqa: D102
        self,
        fullname: str,
    ) -> Callable[[AttributeContext], Type] | None:
        sym = self.lookup_fully_qualified(fullname)
        # Note the dot at the end of the name check to make sure this
        # is an attribute access on proxystore.proxy.Proxy.
        if sym is None and fullname.startswith(f'{PROXY_FULLNAME}.'):
            return proxy_attribute_access
        return None


def _assertion_fallback(function: Callable[P, Type]) -> Callable[P, Type]:
    # Decorator which catches AssertionErrors and returns AnyType
    # to indicate that the plugin does not know how to handle that case
    # and will default back to Any.
    # https://github.com/dry-python/returns/blob/dda187d78fe405d7d1234ffaffc99d8264f854dc/returns/contrib/mypy/_typeops/fallback.py
    @functools.wraps(function)
    def decorator(*args: P.args, **kwargs: P.kwargs) -> Type:
        try:
            return function(*args, **kwargs)
        except AssertionError:
            return AnyType(TypeOfAny.implementation_artifact)

    return decorator


def attribute_access(  # noqa: D103
    instance: Type,
    ctx: AttributeContext,
) -> Type:
    instance = get_proper_type(instance)
    if isinstance(instance, TypeVarType):
        # Don't change anything the we have an unbound Proxy[T].
        return ctx.default_attr_type
    elif isinstance(instance, Instance):
        accessed = instance.copy_modified(args=instance.args)
        exprchecker = ctx.api.expr_checker  # type: ignore
        return analyze_member_access(
            ctx.context.name,  # type: ignore
            accessed,
            ctx.context,
            is_lvalue=False,
            is_super=False,
            is_operator=False,
            msg=ctx.api.msg,
            original_type=instance,
            chk=ctx.api,  # type: ignore
            in_literal_context=exprchecker.is_literal_context(),
        )
    else:
        return ctx.default_attr_type


@_assertion_fallback
def proxy_attribute_access(ctx: AttributeContext) -> Type:  # noqa: D103
    if isinstance(ctx.type, UnionType):
        resolved = tuple(
            attribute_access(instance, ctx) for instance in ctx.type.items
        )
        return UnionType(resolved)
    elif isinstance(ctx.type, Instance):
        # Code somewhat based on:
        # https://github.com/dry-python/returns/blob/560858ec46e529d90267c0c69efdbbce4d417178/returns/contrib/mypy/_features/kind.py#L23
        if len(ctx.type.args) == 0:
            return ctx.default_attr_type
        elif len(ctx.type.args) > 1:
            raise AssertionError(
                f'Got more than one type arg: {ctx.type.args}',
            )
        return attribute_access(ctx.type.args[0], ctx)
    else:
        return ctx.default_attr_type


def plugin(version: str) -> type[ProxyStoreMypyPlugin]:  # noqa: D103
    return ProxyStoreMypyPlugin
