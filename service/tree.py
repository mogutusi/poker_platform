import os

def print_tree(start_path='.', prefix=''):
    entries = sorted(os.listdir(start_path))
    entries = [e for e in entries if e not in ['__pycache__', '.git', '.venv', '.idea','node_modules','versions',"build",'egg-info']]
    
    for index, entry in enumerate(entries):
        path = os.path.join(start_path, entry)
        connector = '└── ' if index == len(entries) - 1 else '├── '
        print(prefix + connector + entry)

        if os.path.isdir(path):
            extension = '    ' if index == len(entries) - 1 else '│   '
            print_tree(path, prefix + extension)

if __name__ == '__main__':

    print_tree(start_path='.')  