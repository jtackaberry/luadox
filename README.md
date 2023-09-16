# LuaDox - Lua Documentation Generator

**👉 [Download the latest release](https://github.com/jtackaberry/luadox/releases/latest)**

📘 You can find an example of LuaDox's output **[here](https://reapertoolkit.dev)**

LuaDox is:
 * born out of personal frustration with LDoc which repeatedly failed to work how I expected/wanted
   (which is perhaps more an indictment of me than of LDoc, as LuaDox is probably also accidentally
   opinionated about structure)
 * an attempt to make nice looking and searchable documentation generated from code
 * written in Python, strangely enough.  Python 3.8 or later is required.
 * *not* strictly compatible with LuaDoc or LDoc tags and not a drop-in replacement, although obviously
   heavily influenced by them

Markdown is used for styling, both in comments as well as standalone manual files,
and `inline code` is implicitly resolved to linkable references (if such a reference
exists).  Standard markdown is supported, plus tables.

A brief example using [middleclass](https://github.com/kikito/middleclass):

```lua
--- Utility class to manipulate files.
--
-- @class xyz.File
-- @inherits xyz.Base
xyz.File = class('xyz.File', xyz.Base)

--- Seek constants.
--
-- These constants can be used with `seek()`.
--
-- @section seekconst
-- @compact

--- Seek from the beginning of the file.
xyz.File.static.SEEK_SET = 'set'
--- Seek from the current position.
xyz.File.static.SEEK_CUR = 'cur'
--- Seek to the end of the file.
xyz.File.static.SEEK_END = 'end'

--- Class API.
--- @section api

--- Opens a new file.
--
-- @example
--   f = xyz.File('/etc/passwd')
--   f.seek(xyz.File.SEEK_END)
--
-- @tparam string name the path to the file to open
-- @tparam string|nil mode the access mode, where `r` is read-only and `w` is read-write.
--   Nil assumes `r`.
-- @treturn xyz.File a new file object
-- @display xyz.File
function xyz.File:initialize(name, mode)
    -- ...
end

--- Seeks within the file.
--
-- @tparam seekconst|nil whence position to seek from, or nil to get current position
-- @tparam number|nil offset the number of bytes relative to `whence` to seek
-- @treturn number byte position within the file
function xyz.File:seek(whence, offset)
  -- ...
end
```

And the simplest possible usage:

```bash
# Linux and OS X
luadox file.lua

# Windows
python luadox file.lua
```

Which assumes a bunch of defaults, one of which is that the output directory `out/` is
created with the rendered documentation.  Obviously this and other customizations can
be configured either by command line arguments and/or config file (see later).


## The Basics

### Documenting Elements

LuaDox ignores standard Lua comments until a block of comments begins with three dashes,
which is the marker that begins a **documentation block**:

```lua
--- This begins a LuaDox documentation block.
--
-- After this point, we can use double dashes.  Anything that follows is
-- considered part of the documentation up until the next non-comment
-- line, which also includes blank lines, whereupon the block terminates.
--
-- Here we declare this comment to be the preamble to a module page.
--
-- @module mymod
```

The above example creates a new *element* (specifically a module element), which
means it is a block of documentation that can be explicitly *referenced*.  In this
case, the reference name is `mymod`, which means elsewhere in documentation (whether
in the same file or another file), this can be linked using one of 3 methods as
shown below:

```lua
--- Here begins another block of documentation.
--
-- This one documents a function, because a function definition immediately follows
-- the comment block.
--
-- Also, we can link to @{mymod} like this, which converts to a hyperlink.  Or you
-- can control the link text @{mymod|so this text links to mymod}.  It's also possible
-- to use inline code markdown like this: `mymod`.
function example()
end
```

### Collections

`@module` (along with `@class`, `@section`, and `@table`) are special types of elements
called *collections*.  Functions and fields that have LuaDox comment marker (i.e. `---`)
preceding their definitions belong to the most recently defined collection element (at
least unless the `@within` tag is used to relocate it somewhere else). Collections show
a summary table of all functions and methods, and then itemize each of them below the
summary table in more detail. In the above example, the `example()` function would belong
directly to the `mymod` collection.

But it's also possible to explicitly create new sections, which are visually delineated
in the rendered documentation:

```lua
--- Special Functions.
--
-- Here we create a new section because of the `@section` tag below.  The first sentence
-- of the comment block is the heading of the section, so it should be short and sweet,
-- and it must end with a period (or some other sentence-ending punctuation like an
-- exclamation point or question mark).
--
-- Anything that follows is text that is included under the section heading.  And of
-- course *standard* **markdown** _is_ [supported](https://lua.org).
--
-- @section specialfuncs

--- Now we're about to document a function.  The blank line just above is very important
-- as it terminates the section block, and begins a new block, which will apply to
-- the function below.
--
-- Now this function will appear within the Special Functions section, because that
-- was the most recent collection element defined.  (It's possible to override which
-- collection this function belongs to without changing the order in the code by
-- using the @within tag.)
function special()
end
```

`@module` and `@class` are special types of collections called *top-level collections*.
This means they are given their own separate pages in the documentation, and also all
elements they contain will have their fully qualified names to be scoped under the
top-level collection.

For example, a field `somefield` in a `@module somemodule` will be fully qualified as
`somemodule.somefield`, which is how it can be referenced from documentation outside the
module.  (`@section` is the exception here: section names are global, and it's up to
you to make them globally unique if you want to be able to reference them from other
pages in the documentation.)


### Functions/Methods

While Lua itself doesn't have explicit classes, LuaDox formalizes terminology such that in
`@class` collections, functions are titled as **methods**, while for `@module` or `@table`
the term **function** is used.

Comment blocks preceding function definitions will add a new function to the current
collection, as seen in the earlier examples.  However it's also possible to define a
function as an assignment:

```lua
--- This will be recognized as a function/method.
xyz.some_function = function(a, b)
   -- ...
end
```

### Fields/Attributes

Documentation preceding an assignment where the rvalue is not a function is treated as a field.
In `@class` collections, fields are labeled as **attributes**.

Fields can be defined anywhere in code: globally, within tables, within functions, etc.  As long
as there is a triple-dash documentation block that immediately precedes a non-function assignment,
it will be added to the current collection as a field.

```lua
--- This will be recognized as a field/attribute
a = 42

whatever = {
   --- This also works, but because "whatever" is not explicitly defined as a
   -- table using the @table tag, this value here is exactly equivalent to the
   -- above example.  In fact, LuaDox will actually log a warning here because
   -- the lvalue "a" is redefined.
   a = 42
}
```

A special case is also handled where the lvalue of the assignment is in the form `self.attr = x`,
specifically when the lvalue is prefixed with `self.`.  Normally the fully qualified lvalue is
included in the documentation, but with `self.attr` the `self` is stripped off and the `attr`
is registered directly within the scope of the current top-level container.

Another special case specific to middleclass is in handling static fields.  When an attribute
defined in a `@class` collection contains the string `.static.` then it will be stripped out.

The example below demonstrates both these special cases:

```lua
--- This class does, well, something.
-- @class xyz.Something
-- @inherits xyz.Superclass
xyz.Something = class('xyz.Superclass')

--- Here the 'static' level will be automatically removed from the attribute name.
xyz.Something.static.MYCONSTANT = 42

function xyz.Something:initialize()
    xyz.Superclass.initialize(self)
    --- This is added as a field directly in the xyz.Something class.
    self.answer = 42
end
```

Note that documentation comments must immediately *precede* field definitions and
cannot be on the same line:

```lua
--- Must precede the definition.
foo = 'bar'

-- Meanwhile ...
foo = 'bar' --- This does NOT work.
```

## Reference Resolution

References that aren't fully qualified (such as `@{this}`) are resolved based on
the scope where the reference was made.  The resolution rules are:
1. Search fields or functions in the current collection
2. If the current collection is a `@section` or `@table`, search up the scope
   stack to the entire `@class` or `@module`
3. Treat the reference as fully qualified, and search the global space for that
   exact name
4. If the top-level collection containing the reference is a `@class`, then search up
   through the class hierarchy as established by `@inherits`

When referencing a function, it's fine to include parens in the reference name.
For example `@{foo()}` or even just markdown inline code `foo()`.


## Tags

It's first important to underline that LuaDox is not LDoc.  Many tags offered by LDoc are
not supported, while many new tags are introduced to provide additional functionality.

Moreover, tags that do intersect between LDoc and LuaDox are not always implemented with
the same syntax or semantics, often because LuaDox extends their functionality.
Consequently, you can expect a bit of a mess trying to pass LDoc-annotated code through
LuaDox, especially when you've delicately structured your code so as to work around the
many quirks of LDoc.

Here is a summary of LuaDox tags, with more details below the table:

| Tag | Type | Description | Example |
|-|-|-|-|
| `@module` | Top-level collection | Declares a module and sets the scope for future documented elements. Modules, like all top-level types, are given separate pages in the rendered documentation. | `@module utils` |
| `@class` | Top-level collection | Like `@module` but for classes, which are also given their own separate documentation pages. See also `@inherits`.  | `@class xyz.SomeClass` |
| `@section` |  Collection | Organizes documented elements such as fields, functions, and tables into a visually distinct group with a heading and arbitrary preamble. Sections can't be nested within other sections; a `@section` tag always creates a *new* section within a top-level collection. | `@section utils.files` |
| `@table` | Nested collection | Declares a new collection containing only fields (not functions like other collections), and allows nesting where field names are fully qualified based on the encapsulating table(s). In most common cases, `@table` isn't needed and `@section` will suffice. | `@table constants` |
| `@inherits` | `@class` modifier | Indicates that the current class is subclassed from another class. This influences how references are resolved (superclasses are searched) and the rendered class page includes a visual of the class hierarchy. | `@inherits xyz.BaseClass` |
| `@tparam` | Function modifier | Documents a typed parameter of the function definition that follows | `@tparam number\|nil w the width of the image, or nil to derive it from height and aspect` |
| `@treturn` | Function modifier | Documents a return value of the function definition that follows | `@treturn bool true if successful, false otherwise` |
| `@see` | Section modifier | Adds a styled "See also" line linking to one or more space-delimited references | `@see ref1 ref2` |
| `@type` | Field modifier | Documents the type of the field definition that follows | `@type table\|nil` |
| `@meta` | Field modifier | Documents arbitrary information for the field definition that follows | `@meta read/write` |
| `@within` | Function/field modifier | Relocates the field or function to another collection while preserving its name. | `@within someothermodule` |
| `@order` | Element modifier | Normally elements are documented in the order they appear in source, but `@order` allows changing the position of an element relative to other elements in the same rendered page. | `@order before somefunc` |
| `@compact` | Collection modifier | Normally, fields and functions in a collection are shown first in summary table form and then broken out later with full documentation. `@compact` controls whether fields and/or functions should *only* show in tabular form. Useful for elements with smaller comments, such as a table of constants. Without arguments, both functions and fields will be shown in compact form, but you can specify `fields` or `functions` as an argument to compact just one of them. | `@compact fields` |
| `@fullnames` | Collection modifier | Normally the table summary of fields and functions are not fully qualified, they are the unqualified short names. This tag ensures the table summary shows the fully qualified name. Commonly combined with `@compact` | `@fullnames` |
| `@display` | Element modifier | Explicitly overrides the display name of an element, but does not affect its name for reference purposes. | `@display MyClass` |
| `@rename` | Element modifier | Overrides *both* the display name and actual name of the element, affecting both its presentation in rendered pages as well as how the element is referenced. | `@rename different_function` |
| `@scope` | Element modifier | Changes the scope of non top-level elements (i.e. functions, fields, and tables, but not classes or modules), affecting both the element's display name and reference name.  A special scope `.` can be used to treat the element as global and will prevent its name from being qualified by the collection it belongs to.  Unlike `@within`, the element is still documented in the same place (class or module page), but its fully qualified name will reflect the given scope name.   | `@scope .` |
| `@alias` | Element modifier | Adds another name by which the element can be referenced. Does not affect the display name. | `@alias fooconsts` |
| `@code` | Code block | Creates a code block with Lua syntax highlighting.  Any contents indented below the `@code` line will be included in the code block. | (See below.) |
| `@example` | Code block | Like `@code` but adds an "Example" heading just above the code block | (See `@code`) |
| `@usage` | Code block | Like `@code` but adds an "Usage" heading just above the code block | (See `@code`) |
| `@note` | Admonition block | Creates a visually distinct text block, useful to highlight notable information. Contents indented below the `@note` tag are included in the block. Can contain nested blocks, such as code blocks or other admonitions. | (See below.) |
| `@warning` | Admonition block | Like `@note` but uses a red color | (See `@note`)
| `@field` | Element | Declare a field within a collection without an explicit field assignment in Lua code.  Rarely needed, and documenting field assignments is preferred and more flexible. Unlike LDoc, must *follow* `@table`. | `@field foo This is the description of the foo field` |
| `@{name}` | Reference | Creates a link to the given element name, using `name` as the link text | `@{fileconsts}` |
| `@{name\|display text}` | Reference | Creates a link to the given element name, but uses `display text` the link text | `@{fileconsts|file constants}` |

### `@module`

Declares a module, which creates a separate page in the rendered documentation, and begins
a new collections for all elements that follow.

While uncommon, it's possible to have multiple `@module` tags in a single source file,
which will result in multiple pages in the documentation.

```lua
--- Common utility functions.
--
-- @module utils
```

### `@class`

Declares a class, which, like `@module`, creates a separate page in the documentation, and
is a collection for subsequent elements.

See also `@inherits`.

```lua
--- Class to manipulate images.
--
-- @class xyz.Image
```

And also like `@module`, it's possible to have multiple `@class` tags in the same file.

### `@section`

Creates a new section within a `@module` or `@class`.  Sections are given their own
visually distinct headings, and are collections for the fields and functions that follow.

The first sentence (terminated with a period, exclamation point, or question mark) is
used as the section heading.  Anything past that is considered as section documentation
below the heading.

**The blank line(s) separating the `@section` block from the elements contained within the
section is necessary.**  This is how LuaDox knows where the documentation for the section
ends and the documentation for a new element (such as a field or function) begins.

```lua
--- Subclass API.
--
-- These functions are not strictly part of the public API, but can be used to create
-- custom subclasses.
--
-- @section subclassapi

--- Reset the state of the object.
function xyz.Widget:_reset()
   -- ...
end
```


### `@table`

Creates a new table collection, which is similar to `@section` but differs in three ways:
1. Nested `@table` are supported, where fully qualified field names are based on the
   full scope of all containing tables (e.g. `foo.bar.baz.field` where `foo`, `bar`, and
   `baz` are nested tables).
2. Only fields are shown. Functions are rendered in documentation as any other field.
3. Unlike `@section`, a blank line isn't needed between the preamble documentation and
   fields, because LuaDox knows to terminate the preamble as soon as the table
   declaration begins.

```lua
-- xyz.os.
--
-- These fields are available immediately upon loading the `xyz` module.
--
-- @table xyz.os
-- @compact
xyz.os = {
    --- true if running on Mac OS X, false otherwise
    mac = (_os == 'osx'),
    --- true if running on Windows, false otherwise
    windows = (_os == 'win'),
    --- true if running on Linux, false otherwise
    linux = (_os == 'lin' or _os == 'oth'),
}
```

### `@inherits`

Used within the context of a `@class` block to declare that the class has been derived
from some other class.  The rendered HTML for the class page will include a tree showing
the full class hierarchy.

The `@inherits` tag takes a single argument that is the name of the immediate superclass.


```lua
--- @class xyz.Subclass
-- @inherits xyz.BaseClass
```

Unqualified references made within the class documentation (all sections, fields,
functions etc. for that class) will search for the name up the class's hierarchy.
If a name is defined in both the current class and one of the superclasses, the
unqualified name will refer to the current class, and a fully qualified name must
be used to link to the superclass's field/function.


### `@tparam`

Defines a typed parameter for the function immediately following the comment block.  The
format of this tag is `@tparam <types> <name> <description>` where:
 * `<types>` is a pipe (`|`) delimited list of possible types, where the type name is
   resolved to a link if possible
 * `<name>` is the name of the parameter from the function signature
 * `<description>` is everything that follows, and which can wrap on multiple lines
   where subsequent lines are indented

 ```lua
 --- Clears the window to a specific color.
 --
 -- @tparam colortype|string|nil color the color to paint the window
 --    background, where nil is black
 function clear(color)
     -- ...
 end
```

Type names can refer to section names as well, which is a convenient way to document
custom complex types such as constants and enums (or Lua approximations thereof).  See the
`seekconst` type from the example at the top of this page.


### `@treturn`

Defines a return value for the function immediately following the comment block.  The
format of this tag is `@treturn <types> <description>` where `<types>` and `<description>`
are the same as that described for `@tparam`.

Multiple `@treturn` tags can be used for functions that return multiple values.

```lua
--- Return the contents of the clipboard.
--
-- @treturn string|nil the clipboard contents, or nil if system clipboard
--    is not available.
-- @treturn string|nil the mimetype of the clipboard contents, or nil if
--    clipboard not available.
function get_clipboard()
   -- ...
end
```

### `@see`

Displays a "See also" line that links to one or more references.  The tag takes multiple reference
names separated by one space.  Function references can optionally include parens, but will always
be displayed with them.

```lua
--- @see xyz.Clipboard:get() get_clipboard() xyz.SomeClass
```

This is slightly different from simply writing the line like "See also `get_clipboard()`"
as it is wrapped in a div with class `see` that can be customized in CSS.

### `@type`

Used in the comment block preceding a field definition and defines the field's type.  This
tag takes the form `@type <types>` and, like `@tparam` and `@treturn`, the types argument
is a pipe (`|`) delimited list of possible types, which will be resolved into links if
possible.

Field types are shown both in the summary table as well as the full detailed field list.

```lua
--- If true, scrolling smoothly animates, while false scrolls in steps.  Nil will use the
-- global default
-- @type bool|nil
smooth_scroll = nil
```

### `@meta`

Like `@type`, this is used in comment blocks preceding field definitions and can be used
to communicate any arbitrary custom thing, however unlike `@type` it also works for
functions.  This tag takes the form `@meta <anything>` where `<anything>` is a string that
is allowed to contain spaces.

The meta value is displayed alongside any defined types via `@type` in both the summary
table as well as the detailed field list.

A useful application of `@meta` is to indicate whether the field/attribute in question is
considered read-only or read/write as far as the API caller is concerned.

```lua
-- The "defaults" table isn't meaningful here as far as documentation is concerned.
-- This is just a regular comment, not a triple-dash documentation block, so LuaDox
-- ignores it.  Fields defined and documented inside this table are added directly
-- to the current collection.
defaults = {
   --- The current width of the window which is updated when the user resizes the window.
   -- @type number
   -- @meta read-only
   w = 640
}
```

### `@within`

Can be included in a documentation block preceding a function or field definition to relocate
it to some other collection, while preserving the original name for display purposes.  The tag
takes the form `@within <name>` where `<name>` is the name of any collection, such as a
`@class`, `@module`, or -- more usefully -- a `@section`, either in the same class or module,
or some other.

This can be used to affect the location of a field or function in documentation without needing
to reorder your code.  If you want exact control of the location relative to other fields or
functions in that collection, you can use `@order`.

```lua
--- Called when the widget's position needs to be recalculated.
-- @within subclassapi
function xyz.Widget:_layout()
end

-- ... other stuff ...

--- API available to subclasses.
-- @section subclassapi
```

This is a bit of a rare case, but if the collection being targeted by `@within` itself has a
`@rename` tag, the collection name that `@within` needs to reference is the pre-renamed
name of the target collection.

### `@order`

Affects where the element (whether field, function, table, or section) appears in the rendered
documentation.  This isn't used to move a field or function to another collection -- use
`@within` for that -- but it changes the location of the element relative to its siblings in
the collection.

Non top-level collections (i.e. `@section` and `@table`) can also be reordered relative to one
another in the same module or class.

The tag takes the form `@order <whence> [<anchor>]` where `<whence>` is one of:
 * `before`: moves the element *before* the given (fully qualified) anchor element
 * `after`: moves the element *after* the given (fully qualified) anchor element
 * `first`: make the element the first one in the collection (and where `<anchor>` is not needed)
 * `last`: makes the element the last one in the collection (and where `<anchor>` is not needed)

 ```lua
 --- Draws the widget.
 -- @within subclassapi
 -- @order after xyz.Widget:_layout
function xyz.Widget:_draw()
end
```

### `@compact`

Collections include a summary table of fields and functions within the collection, where
each element includes only the first sentence from their documentation, before enumerating
the full list of elements with their full documentation blocks below the summary table.

The `@compact` tag is used to skip the more detailed list, showing only the tabular form.
In this case, the full documentation is included in the table, not just the first sentence.

The tag takes an optional argument, either `field` or `function` that skips the full detailed
list for one or the other type of element.  If the argument is omitted, both fields and
functions are shown only in tabular form.

See `@fullnames` below for a combined example.

### `@fullnames`

Normally a collection's table summary of fields and functions displays the unqualified
short name. This tag, which takes no arguments, causes the table view to display the fully
qualified name instead.

```lua
--- Seek constants.
--
-- These constants can be used with `seek()`.
--
-- @section seekconst
-- @compact
-- @fullnames

--- Seek from the beginning of the file.
xyz.File.static.SEEK_SET = 'set'
--- Seek from the current position.
xyz.File.static.SEEK_CUR = 'cur'
--- Seek to the end of the file.
xyz.File.static.SEEK_END = 'end'
```

### `@display`

Affects how the element is displayed in documentation, but doesn't alter how the element
is referenced.  This tag takes the form `@display <name>` where `<name>` is the overridden
display name.

One use case is to change the name of middleclass initializers, where the class is invoked
directly to construct a new instance:

```lua
--- Creates a new widget with the given attributes.
-- @display xyz.Widget
function xyz.Widget:initialize(attrs)
```

### `@rename`

Like `@display` in that it changes the element's display name in documentation, but *also* changes
the name for references.

### `@scope`

Element names are normally qualified based on their containing class, module, or table.
For example, a field `bar` defined in a `@class Foo` would be fully qualified as
`Foo.bar`. However, the `@scope` tag can override the containing scope -- `Foo` in this
case -- with any arbitrary symbol.  This affects how the element is both displayed and how
it's referenced, however doesn't change which collection the element appears in. (Use
`@within` for that.)

The tag takes the form `@scope <name>` where `<name>` replaces the element's normal scope name.
A special scope `.` (single dot) will treat the element as global, preventing it from being
qualified by anything: the field or function will be considered as global both in how it's
displayed and referenced.

```lua
--- Miscellaneous utilities.
-- @module utils

--- Normally this field would be qualified as utils.MYCONST, but this makes it appear
-- as a global value, and can be referenced elsewhere as @{MYCONST}
-- @scope .
MYCONST = 42
```

### `@alias`

Adds another name by which the element can be referenced elsewhere in documentation.  The
display name is unchanged, and the element's normal name can still be used for references.
This merely adds an additional name for references.


### `@code`

Renders a code block with Lua syntax highlighting in the documentation.  The tag takes no
arguments, but any commented lines indented within @code are included in the code block.
The code block terminates as soon as a line is has less indentation than the first line
under the `@code` tag.

```lua
--- Some function to do a thing.
--
-- This might be some example usage:
-- @code
--    -- This is actually a comment in the code block.
--    -- Subsequent lines indented at this level are included in the block.
--    local x = do_a_thing()
--
-- Now that this line is indented less than the first line under @code, this will
-- *not* be included in the code block, but will start a new paragraph underneath
-- it. The blank line separating this paragraph and the code block isn't significant,
-- only the indentation level matters.
function do_a_thing()
    -- ...
end
```

### `@example`

Like `@code`, and works exactly the same way in terms of the semantics of indendation, but adds a
heading Example" above the syntax-highlighted code block.

### `@usage`

Like `@example`, but the heading says "Usage" instead.

### `@note`

Creates a visually distinct paragraph (bordered with a green background color), which can
be used to emphasize noteworthy content.

This tag takes the form `@note <title>` where `<title>` is an *optional* arbitrary string
(which can include spaces) that acts as the title of the block.  Indentation controls the
contents of the block, exactly as `@code` works.

Nesting is possible, including (and most usefully) `@code` blocks which can appear
within admonitions.


```lua
--- This is the start of a normal documentation block.
--
-- Some standard documentation content would go here.
--
--  @note This is the title of the block
--    Now anything indented at this level is included within the admonition paragraph.
--    That includes this line, but not the next one.
--
--    @code
--       -- This is a nested code block inside the note.
--       foo()
--
-- This line is dedented relative to the first line under @note so it starts a normal
-- paragraph at the same level as the first one.
```

### `@warning`

Exactly like `@note` but uses a red background instead of green so is useful for warning
or cautionary content.

### `@field`

Adds a field to the current collection purely a comment, without the need for a line of
Lua code to declare and assign the field.

This tag takes the form `@field <name> <description>` where `<name>` is the name of the field
and `<description>` is an arbitrary, single line description of the field.

```lua
--- @field level The current log level.
```

The above is semantically equivalent to this:

```lua
--- The current log level.
level = nil
```

Generally the second form above is preferred, because it allows for multiple lines and
even paragraphs of comments, as well as field modifiers such as `@type` and `@meta`.

Unlike LDoc, `@field` must follow a `@table` definition:

```lua
--- Current mouse state.
-- @table mouse
-- @field x the x coordinate of the cursor
-- @field y the y coordinate of the cursor
-- @field button the current mouse button pressed
```

### Reference tags

Reference tags are used to create hyperlinks in the rendered documentation to any element
in any file.  Reference tags can take either of these forms:
1. `@{name}`: resolve `name` per the reference resolution rules described earlier, and use
   the fully qualified form of the reference name as the link text (even if `name` itself
   is not fully qualified)
2. `@{name|link text}`: resolve `name` but use the given `link text` instead of the
   fully qualified name of the reference.

Although not a tag, if the contents of markdown `inline code` is a resolvable name, it
will be rendered as a hyperlink (still with preformatted text), but unlike `@{name}` which
uses the fully qualified form, the with `inline code` the hyperlink text will be as
written.

## Manual Pages

Arbitrarily many separate custom markdown files can be included in the rendered
documentation. They are defined in the `[manual]` section of the config file, or can be
passed using the `-m` or `--manual` command line argument.

Each document is defined in the form `id=filename.md` where `id` is the top-level scope
name for reference purposes (see later), and also dictates the name of the rendered html
file.

Consider this configuration, for example:

```
[manual]
index=intro.md
tutorial=tut.md
```

This will add both pages to the manual.  **index** is a special id, which is written
as the root `index.html` in the rendered documentation, and is also linked from the
topbar on every page.

Suppose our `intro.md` looked like:

```markdown
# Introduction

Some introductory paragraph.  By the way, images are supported:

![](img/foo.png)

The image is relative to the path of the current file.  It's up to you to
copy the `img/` directory to the rendered documentation output directory
after.

## How to install

This is a preamble paragraph on installation.

### Linux

How to install on Linux ...

### OS X

How to install on a Mac ...

### Windows

Sorry about your luck ...

#### This is a level 4 heading

Nothing very interesting here.
```

Within the markdown, the level 1 heading dictates the title of the manual page, which
is used in the Manual section of the sidebar, as well as the HTML title for the manual
page.  In the above example, that's "Introduction".

Level 2 and level 3 headings are included in the table of contents in the sidebar.

### References and Manual Pages

The manual page id (e.g. `index` and `tutorial` in the example above) is the top-level
symbol.  You will want to make sure you pick an id that doesn't conflict with any
`@module` or `@class` name from the documentation, as these all share the top-level
namespace.

Level 1, 2, and 3 headings are names subordinate to the id, and are converted to slugs by
converting everything to lowercase, removing all punctuation, and replacing spaces with
underscores.

For example, `index.how_to_install` or `index.linux`.  This name can be referenced from
code, and also other manual pages.  The `@{name}` and `@{name|link text}` reference tags
are supported in manual pages as well.


## Execution

LuaDox is distributed as a single binary that can be downloaded [on the release
page](https://github.com/jtackaberry/luadox/releases/latest).  On Linux and OS X, the
binary can be executed directly:

```bash
$ luadox -c luadox.conf
```

But on Windows, Python must be called directly (and of course this also works on
Linux and OS X):

```
C:\src\luadox> python luadox -c luadox.conf
```

`luadox --help` will output usage instructions:

```
usage: luadox [-h] [-c FILE] [-n NAME] [-o DIRNAME] [-m [ID=FILENAME [ID=FILENAME ...]]]
              [--css FILE] [--favicon FILE] [--nofollow] [--encoding CODEC] [--version]
              [FILE [FILE ...]]

positional arguments:
  [MODNAME=]FILE        List of files to parse or directories to crawl
                        with optional module name alias

optional arguments:
  -h, --help            show this help message and exit
  -c FILE, --config FILE
                        Luadox configuration file
  -n NAME, --name NAME  Project name (default Lua Project)
  -o DIRNAME, --outdir DIRNAME
                        Directory name for rendered files, created if necessary (default ./out)
  -m [ID=FILENAME [ID=FILENAME ...]], --manual [ID=FILENAME [ID=FILENAME ...]]
                        Add manual page in the form id=filename.md
  --css FILE            Custom CSS file
  --favicon FILE        Path to favicon file
  --nofollow            Disable following of require()'d files (default false)
  --encoding CODEC      Character set codec for input (default UTF-8)
  --version             show program's version number and exit
```

The positional `[MODNAME=]FILE` argument(s) defines what source files to scan.  The
`FILE` part can be either specific Lua source files, or directories within which
`init.lua` exists.  By default, LuaDox will follow and parse all files that are
`require()`d within the code, provided the required file is discovered within any of the
directories containing the files passed on the command line.

The optional `MODNAME` part of the argument explicitly specifies the Lua module name as
`require()`d in code.  For example, if your library is called `foo` and your source files
are held in `../src/foo` then LuaDox knows that when requiring `foo.bar.baz` from Lua, we
should check `../src/foo/bar/baz.lua` because of the matching `foo` component between the
module name and the path.

However, if all your source files for module `foo` were instead contained in `../src`,
say, you need to tell LuaDox that requiring `foo.bar` is actually at `../src/bar.lua`.
This is done by specifying `MODNAME` in the argument, i.e. `foo=../src`.

Bottom line: if your directory structure is directly named after the module name, you
probably don't need to specify the `MODNAME` alias, but if your directory is called
something else, like `src`, you do.

The behavior to automatically discover and parse `require()`d files can be disabled with
the `--nofollow` argment or setting `follow = false` in the config file, in which case
LuaDox will only parse files explicitly passed.

Most options can be defined on the command line, but it may be more convenient to
use a config file.

Config files are ini-style files that define these sections:
* `[project]` for project level settings
* `[manual]` for manual pages where each page is a separate `id=filename` line
* `[link<n>]` for user-defined custom links that appear on the center of
   each page, and where `<n>` is a number that controls the order.

Here's an annotated example `luadox.conf` that describes the available config
properties.  All properties are optional except for files (although files could
also be passed on the command line if you prefer).

```ini
[project]
# Project name that is displayed on the top bar of each page
name = My Lua Project | Where Awesome Things Happen
# HTML title that is appended to every page. If not defined, name is used.
title = My Lua Project
# A list of files or directories for LuaDox to parse.  Globs are supported.
# This can be spread across multiple lines if you want, as long as the
# other lines are indented.
files = ../app/rtk/widget.lua ../app/rtk/
# The directory containing the rendered output files, which will be created
# if necessary.
outdir = html
# Path to a custom css file that will be included on every page.  This will
# be copied into the outdir.
css = custom.css
# Path to a custom favicon. This will be copied into the outdir.
favicon = img/favicon.png
# If require()d files discovered in source should also be parsed.
follow = true
# Character encoding for input files, which defaults to the current system
# locale.  Output files are always utf8.
encoding = utf8

[manual]
# Custom manual pages in the form: id = filename.
#
# The ids must not conflict with any class or module name otherwise references
# will not properly resolve.
index = intro.md
tutorial = tut.md

[link1]
icon = download
text = Download
url = {root}index.html#download

[link2]
icon = github
text = GitHub
url = https://github.com/me/myproject
```

Link sections are optional. Each section takes these options:
 * `text` (required): the link's text
 * `url` (required):
 * `icon` (optional): the name of a built-in icon, or path to a custom image file.
     Currently supported built-in icon names are `download`, `github`, `gitlab`, and
     `bitbucket`.  If the value isn't one of the built-in names then it's treated as
     a path, where `{root}` will be replaced with the relative path to the document
     root.
 * `tooltip` (optional): the tooltip text that appears when the mouse hovers over
    the hyperlink.

User-defined links currently can't be specified on the command line, they must
be defined in the config file.

## Docker Image

LuaDox is also available as a [Docker image on Docker Hub](https://hub.docker.com/r/jtackaberry/luadox):

```bash
$ docker run -v ~/src/myproject:/project -w /project/doc jtackaberry/luadox luadox -c luadox.conf
```

Of course, that's a bit cumbersome, having to set up the volume mount and
working directory, so for command line use the release binary is probably more
convenient.  However the Docker image can be useful when generating
documentation as part of a CI/CD pipeline, such as GitHub Actions.
