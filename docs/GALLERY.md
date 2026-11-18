# Gallery

Five extractions Towel 2.0 makes in published open-source projects that are not
part of [its validation](VALIDATION.md). Each command runs on the module exactly
as the project released it; the excerpt shows the change the text describes,
and `...` marks output left out. Each runs without type checking and
formatting, so the result does not depend on the tools installed where Towel
runs.

## A check repeated in two operators

[isodate](https://pypi.org/project/isodate/0.7.2/) 0.7.2 (BSD-3-Clause), `src/isodate/duration.py`. `Duration.__add__` and `Duration.__rsub__` refuse fractional years and months with the same four lines.

```bash
towel preview isodate/duration.py --no-types --no-format
```

```diff
Would apply 1 refactoring(s).
Analysed 1 Python file (17 functions).
Would change 1 file(s):
  duration.py: 1 refactoring(s)
    - Extract common code from __add__ and __rsub__
--- before/duration.py
+++ after/duration.py
@@ -160,10 +160,7 @@
             # try anything that looks like a date or datetime
             # 'other' has attributes year, month, day
             # and relies on 'timedelta + other' being implemented
-            if not (float(self.years).is_integer() and float(self.months).is_integer()):
-                raise ValueError(
-                    "fractional years or months not supported" " for date calculations"
-                )
+            self.__extracted_func_0()
             newmonth = other.month + self.months
             carry, newmonth = fquotmod(newmonth, 1, 13)
             newyear = other.year + self.years + carry
@@ -245,10 +242,7 @@
         try:
             # check if other behaves like a date/datetime object
             # does it have year, month, day and replace?
-            if not (float(self.years).is_integer() and float(self.months).is_integer()):
-                raise ValueError(
-                    "fractional years or months not supported" " for date calculations"
-                )
+            self.__extracted_func_0()
             newmonth = other.month - self.months
             carry, newmonth = fquotmod(newmonth, 1, 13)
             newyear = other.year - self.years + carry
@@ -314,3 +308,9 @@
         if start is not None:
             return (start + self) - start
         return end - (end - self)
+
+    def __extracted_func_0(self):
+        if not (float(self.years).is_integer() and float(self.months).is_integer()):
+            raise ValueError(
+                "fractional years or months not supported" " for date calculations"
+            )
Input files were not changed.
```

The check becomes a method of `Duration`, private to the class, and both operators call it. The comments before each copy stay where they were.
## Range checks in a family of validators

[configobj](https://pypi.org/project/configobj/5.0.9/) 5.0.9 (BSD-3-Clause), `src/configobj/validate.py`. `is_integer` and `is_float` end with the same bounds check, and the list validators share a length check.

```bash
towel preview configobj/validate.py --no-types --no-format
```

```diff
Would apply 3 refactoring(s).
Analysed 1 Python file (39 functions).
Would change 1 file(s):
  validate.py: 3 refactoring(s)
    - Extract common code from is_list and is_string
    - Extract common code from is_integer and is_float
    - Extract common code from __extracted_func_0 and is_mixed_list
--- before/validate.py
+++ after/validate.py
@@ -244,6 +244,30 @@
 _matchstring = '^%s*' % _paramstring


+def __extracted_func_2(value):
+    try:
+        num_members = len(value)
+    except TypeError:
+        raise VdtTypeError(value)
+    return num_members
+
+
+def __extracted_func_1(value, min_val, max_val):
+    if (min_val is not None) and (value < min_val):
+        raise VdtValueTooSmallError(value)
+    if (max_val is not None) and (value > max_val):
+        raise VdtValueTooBigError(value)
+    return value
+
+
+def __extracted_func_0(value, min_len, max_len):
+    num_members = __extracted_func_2(value)
+    if min_len is not None and num_members < min_len:
+        raise VdtValueTooShortError(value)
+    if max_len is not None and num_members > max_len:
+        raise VdtValueTooLongError(value)
+
+
 def dottedQuadToNum(ip):
     """
     Convert decimal dotted quad string to long integer
@@ -802,11 +826,7 @@
             value = int(value)
         except ValueError:
             raise VdtTypeError(value)
-    if (min_val is not None) and (value < min_val):
-        raise VdtValueTooSmallError(value)
-    if (max_val is not None) and (value > max_val):
-        raise VdtValueTooBigError(value)
-    return value
+    return __extracted_func_1(value, min_val, max_val)


 def is_float(value, min=None, max=None):
@@ -854,11 +874,7 @@
             value = float(value)
         except ValueError:
             raise VdtTypeError(value)
-    if (min_val is not None) and (value < min_val):
-        raise VdtValueTooSmallError(value)
-    if (max_val is not None) and (value > max_val):
-        raise VdtValueTooBigError(value)
-    return value
+    return __extracted_func_1(value, min_val, max_val)


 bool_dict = {
...
Input files were not changed.
```

Three helpers come out of one run: the bounds check, the length check, and, on a later pass, the `len()` guard the length check itself repeats. Towel keeps going until nothing more can be extracted.
## Three copies of a proxy swap

[jsonpickle](https://pypi.org/project/jsonpickle/4.1.4/) 4.1.4 (BSD-3-Clause), `jsonpickle/unpickler.py`. Three methods of `Unpickler` finish by resetting a proxy, swapping the reference and returning the restored object, each under a different name.

```bash
towel preview jsonpickle/unpickler.py --no-types --no-format
```

```diff
Would apply 2 refactoring(s).
Analysed 1 Python file (51 functions).
Would change 1 file(s):
  unpickler.py: 2 refactoring(s)
    - Extract common code from _restore_reduce and _restore_object
    - Extract code repeated in _restore_dict
--- before/unpickler.py
+++ after/unpickler.py
@@ -561,9 +561,7 @@
             reduce_val = list(map(self._restore, obj[tags.REDUCE]))
         except TypeError:
             result = []
-            proxy.reset(result)
-            self._swapref(proxy, result)
-            return result
+            return self.__extracted_func_0(proxy, result)
         if len(reduce_val) < 5:
             reduce_val.extend([None] * (5 - len(reduce_val)))
         f, args, state, listitems, dictitems = reduce_val
@@ -619,9 +617,7 @@
             for k, v in dictitems:
                 stage1.__setitem__(k, v)

-        proxy.reset(stage1)
-        self._swapref(proxy, stage1)
-        return stage1
+        return self.__extracted_func_0(proxy, stage1)

     def _restore_id(self, obj):
         try:
@@ -893,9 +889,7 @@
             proxy = _Proxy()
             self._mkref(proxy)
             instance = handler(self).restore(obj)
-            proxy.reset(instance)
-            self._swapref(proxy, instance)
-            return instance
+            return self.__extracted_func_0(proxy, instance)

         if cls is None:
             self._process_missing(class_name)
...
@@ -1010,3 +999,14 @@
         else:
             restore = _passthrough
         return restore
+
+    def __extracted_func_0(self, proxy, _param_0):
+        proxy.reset(_param_0)
+        self._swapref(proxy, _param_0)
+        return _param_0
+
+    def __extracted_func_1(self, result, data, k):
+        if isinstance(result, _Proxy):
+            self._proxies.append((data, k, result, _obj_setvalue))
+
+        self._namestack.pop()
Input files were not changed.
```

The object each copy restored, `result`, `stage1` or `instance`, became the parameter `_param_0`; a block repeated twice inside `_restore_dict` became a second helper.
## A list and a dict comprehension

[asteval](https://pypi.org/project/asteval/1.0.10/) 1.0.10 (MIT), `asteval/asteval.py`. The interpreter evaluates list and dict comprehensions with the same nine lines, differing only in whether the result starts as `[]` or `{}`.

```bash
towel preview asteval/asteval.py --no-types --no-format
```

```diff
Would apply 4 refactoring(s).
Analysed 1 Python file (68 functions).
Would change 1 file(s):
  asteval.py: 4 refactoring(s)
    - Extract common code from on_listcomp and on_dictcomp
    - Extract common code from node_assign and _comp_save_syms
    - Extract common code from do_generator and on_functiondef
    - Extract common code from node_assign and on_functiondef
--- before/asteval.py
+++ after/asteval.py
...
@@ -864,15 +855,7 @@

     def on_listcomp(self, node):
         """List comprehension v2"""
-        saved_syms, delete_syms = self._comp_save_syms(node)
-
-        out = []
-        self.do_generator(node.generators, node, out)
-        for name, val in saved_syms.items():
-            self.symtable[name] = val
-        for name in delete_syms:
-            self.symtable.pop(name, None)
-        return out
+        return self.__extracted_func_0(node, [])

     def on_setcomp(self, node):
         """Set comprehension"""
@@ -880,15 +863,7 @@

     def on_dictcomp(self, node):
         """Dict comprehension v2"""
-        saved_syms, delete_syms = self._comp_save_syms(node)
-
-        out = {}
-        self.do_generator(node.generators, node, out)
-        for name, val in saved_syms.items():
-            self.symtable[name] = val
-        for name in delete_syms:
-            self.symtable.pop(name, None)
-        return out
+        return self.__extracted_func_0(node, {})

     def on_excepthandler(self, node):  # ('type', 'name', 'body')
         """Exception handler..."""
...
@@ -1035,10 +1007,35 @@
...
+    def __extracted_func_0(self, node, _param_0):
+        saved_syms, delete_syms = self._comp_save_syms(node)
+
+        out = _param_0
+        self.do_generator(node.generators, node, out)
+        for name, val in saved_syms.items():
+            self.symtable[name] = val
+        for name in delete_syms:
+            self.symtable.pop(name, None)
+        return out
...
Input files were not changed.
```

Each caller passes its empty container, `[]` or `{}`, as an ordinary argument: a fresh empty container, evaluated once before the helper runs, needs no `lambda`.
## Clipboard commands on Linux

[pyperclip](https://pypi.org/project/pyperclip/1.11.0/) 1.11.0 (BSD-3-Clause), `src/pyperclip/__init__.py`. The `xclip` and `xsel` back ends copy text by running a command with the same `Popen` call; the paste functions repeat another.

```bash
towel preview pyperclip/__init__.py --no-types --no-format
```

```diff
Would apply 2 refactoring(s).
Analysed 1 Python file (47 functions).
Would change 1 file(s):
  __init__.py: 2 refactoring(s)
    - Extract common code from paste_xclip and paste_wl
    - Extract common code from copy_xclip and copy_xsel
--- before/__init__.py
+++ after/__init__.py
@@ -81,6 +81,23 @@
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE) == 0
     _executable_exists = _py2_executable_exists

+
+def __extracted_func_1(text, _param_0, _param_1, _param_2):
+    p = subprocess.Popen([_param_0, _param_1, _param_2],
+                         stdin=subprocess.PIPE, close_fds=True)
+    p.communicate(input=text.encode(ENCODING))
+
+
+def __extracted_func_0(__param_0):
+    p = subprocess.Popen(__param_0(),
+                         stdout=subprocess.PIPE,
+                         stderr=subprocess.PIPE,
+                         close_fds=True)
+    stdout, stderr = p.communicate()
+    # Intentionally ignore extraneous output on stderr when clipboard is empty
+    return stdout.decode(ENCODING)
+
+
 # Exceptions
 class PyperclipException(RuntimeError):
     pass
@@ -164,21 +181,13 @@
         selection=DEFAULT_SELECTION
         if primary:
             selection=PRIMARY_SELECTION
-        p = subprocess.Popen(['xclip', '-selection', selection],
-                             stdin=subprocess.PIPE, close_fds=True)
-        p.communicate(input=text.encode(ENCODING))
+        __extracted_func_1(text, 'xclip', '-selection', selection)

     def paste_xclip(primary=False):
         selection=DEFAULT_SELECTION
         if primary:
             selection=PRIMARY_SELECTION
-        p = subprocess.Popen(['xclip', '-selection', selection, '-o'],
-                             stdout=subprocess.PIPE,
-                             stderr=subprocess.PIPE,
-                             close_fds=True)
-        stdout, stderr = p.communicate()
-        # Intentionally ignore extraneous output on stderr when clipboard is empty
-        return stdout.decode(ENCODING)
+        return __extracted_func_0(lambda: ['xclip', '-selection', selection, '-o'])

     return copy_xclip, paste_xclip

@@ -192,9 +201,7 @@
         selection_flag = DEFAULT_SELECTION
         if primary:
             selection_flag = PRIMARY_SELECTION
-        p = subprocess.Popen(['xsel', selection_flag, '-i'],
-                             stdin=subprocess.PIPE, close_fds=True)
-        p.communicate(input=text.encode(ENCODING))
+        __extracted_func_1(text, 'xsel', selection_flag, '-i')

     def paste_xsel(primary=False):
         selection_flag = DEFAULT_SELECTION
...
Input files were not changed.
```

The copy helper takes the command's three words as arguments. The paste helper takes the command as `lambda: [...]`, because in one copy it is a list built from a variable and in the other a variable, and a parameter takes one form for every call. The helpers land before the module's `# Exceptions` comment, which stays with the class it introduces.

## Try your own

`towel preview` writes nothing, so it is safe to point at any project. Give it
your package or `src/` directory; here `isodate/` holds only `duration.py`, the
module this page started with:

```bash
towel preview isodate/ --summary --no-types --no-format
```

```text
Would apply 1 refactoring(s).
Analysed 1 Python file (17 functions).
Would change 1 file(s):
  duration.py: 1 refactoring(s)
    - Extract common code from __add__ and __rsub__
Input files were not changed.
```

Add `--explain` to see which files contain module-level statements or early-return
guards that Towel does not examine. [Why something was left alone](WHY_LEFT_ALONE.md#when-towel-finds-nothing-to-do)
explains those limits. The [quick start](QUICKSTART.md) takes a change from
preview to a tested commit.

[Documentation index](README.md)
