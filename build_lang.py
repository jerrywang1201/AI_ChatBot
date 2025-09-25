from tree_sitter import Language

Language.build_library(
    'build/my-languages1.so',

    [
        'tree-sitter-cpp'
    ]
)