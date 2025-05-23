import sublime
import sublime_plugin
import threading
import os
import re
import subprocess

log_enable = False

def debug_log(string):
    if log_enable:
        print(string)

class SimpleFuzzyDebugToggleCommand(sublime_plugin.WindowCommand):
    def run(self):
        global log_enable
        log_enable = not log_enable

class EditorLineInputHandler(sublime_plugin.ListInputHandler):
    def __init__(self, view):
        self.view = view
        self._backup_region = view.sel()[0]
        self._init = True

    def name(self):
        return "pos"

    def placeholder(self):
        return "Search content line..."

    def list_items(self):
        regions = self.view.find_all('.+\n')
        lines = [self.view.substr(region).strip().replace('\t', '') for region in regions]
        positions = [r.begin() for r in regions]
        return [
            sublime.ListInputItem(
                text=line_str,
                value=pos,
            ) for pos, line_str in zip(positions, lines)
            if re.match('\s*\d+$', line_str) is None and len(line_str)
        ]

    def cancel(self):
        self.view.sel().clear()
        self.view.sel().add(self._backup_region)
        self.view.show_at_center(self._backup_region)

    def preview(self, pos):
        if self._init and pos == 0:
            return
        self._init = False
        row, col = self.view.rowcol(pos)
        self.view.run_command("goto_line", {"line": row+1})

class FuzzyCurrentFileCommand(sublime_plugin.TextCommand):
    def run(self, edit, pos):
        row, col = self.view.rowcol(pos)
        self.view.run_command("goto_line", {"line": row+1})

    def input(self, args):
        if "pos" not in args:
            return EditorLineInputHandler(self.view)
        else:
            return None

class GrepFileLinesThread(threading.Thread):
    def __init__(self, folder, filename, encoding='UTF-8', timeout=30):
        self.folder = folder
        self.filename = filename
        self.encoding = encoding
        self.rel_filename = self.filename.replace(folder, '')
        self.timeout = timeout
        self.result = None
        threading.Thread.__init__(self)

    def run(self):
        self.result = self._read_filelines(self.filename)

    def _read_filelines(self, filename):
        with open(self.filename, 'r', encoding=self.encoding) as fs:
            try:
                lines = [
                    l.strip().replace('\t', '')
                    for l in fs.readlines()
                ]
                return [
                    sublime.ListInputItem(
                        text=line_str,
                        value=(self.filename, line_no + 1),
                        annotation='%s:%s'%(self.rel_filename, line_no+1),
                    ) for line_no, line_str in enumerate(lines)
                    if len(line_str) > 0
                ]
            except UnicodeDecodeError:
                return []

def _await_view_goto_line(view, line):
    if view.is_loading():
        sublime.set_timeout_async(lambda: _await_view_goto_line(view, line), 50)
        return
    # wait for view rendering current line in center
    sublime.set_timeout_async(lambda: view.run_command("goto_line", {"line": line}), 10)



def get_open_file_paths():
    window = sublime.active_window()
    views = window.views()
    file_paths = [v.file_name() for v in views if v.file_name()]
    return file_paths
class FolderLineInputHandler(sublime_plugin.ListInputHandler):

    def __init__(self, window, source="active_folder"):
        self.window = window
        self.view = self.window.active_view()
        self._backup_region = self.view.sel()[0]
        self._init = True
        self.source = source

    def name(self):
        return "file_lines"

    def placeholder(self):
        return "Search content line..."

    def cancel(self):
        self.window.focus_view(self.view)
        self.view.sel().clear()
        self.view.sel().add(self._backup_region)
        self.view.show_at_center(self._backup_region)

    def preview(self, file_lines):
        file = file_lines[0]
        line = file_lines[1]
        view = self.window.open_file(file, sublime.TRANSIENT)
        _await_view_goto_line(view, line)

    def list_items(self):

        print(self.source)
        # todo: implement an active_folder_no_recurse also
        encoding = self.view.encoding() if self.view.encoding() != 'Undefined' else 'UTF-8'
        if (self.source) =="opened_files":
            file_list = get_open_file_paths()

        elif (self.source) =="active_folder":
            folders = self.window.folders()
            if len(folders) == 0:
                # sublime.error_message('No project folder found for Fuzzy Project Line search.')
                # return []

                # if no folder/workspace, take the current files directory
                file_path = self.view.file_name()
                if file_path:
                    folders = [os.path.dirname(file_path)]
                else:
                    return []
            print('ttttt')
            print(f"folders len: {len(folders)}")
            print(f"folders: {folders}")
            active_folder = next(
                (f for f in folders if f in (self.view.file_name() or '')),
                folders[0]
            )
            debug_log('fuzzy project in: %s with Encoding=%s'%(active_folder, encoding))
            file_list = self._list_files(active_folder, encoding)

        

        threads = []
        lines = []
        for file in file_list:
            active_folder= os.path.dirname(file)
            if not os.path.exists(file):
                continue
            view = self.window.find_open_file(file)
            if view == None:
                thread = GrepFileLinesThread(active_folder, file, encoding)
                thread.start()
                threads.append(thread)
            else:
                lines += self._grep_view_lines(active_folder, view)

        for thread in threads:
            thread.join()
            lines += thread.result

        return lines

    # return filenames including folder name
    def _list_files(self, folder, encoding='UTF-8'):
        print('test')
        user_pref_cmd = self.view.settings().get('simple_fuzzy_ls_cmd', '')
        user_pref_chk = self.view.settings().get('simple_fuzzy_chk_cmd', '')

        def _fmt_cmd(fmt):
            return '{_fmt}'.format(_fmt=fmt).format(folder=folder)

        def _ls_dir(check_cmd, ls_cmd):
            OK = 0
            if len(check_cmd):
                debug_log('check_cmd: {!r}'.format(_fmt_cmd(check_cmd)))
                if os.system(_fmt_cmd(check_cmd)) != OK:
                    return []
            debug_log('ls_cmd: {!r}'.format(_fmt_cmd(ls_cmd)))
            try:
                f_list = subprocess.check_output(_fmt_cmd(ls_cmd), shell=True).splitlines()
            except subprocess.CalledProcessError:
                f_list = []
            debug_log('ls_cmd: {} file(s) found'.format(len(f_list)))
            return [f.decode(encoding) for f in f_list]

        def _builtin_ls():
            # default fallback for listing files in folder
            f_list = []
            for root, dirs, files in os.walk(folder):
                f_list += [os.path.join(root, f) for f in files]
            return f_list

        # todo: if no active  project, it looks in current file's dir(so git ls might not be apt here)
        default_cmds = {
            'rg': lambda: _ls_dir('', 'rg --files "{folder}"'),
            'git': lambda: _ls_dir('git -C "{folder}" status', 'git -C "{folder}" ls-files'),
            'built-in': _builtin_ls,
        }

        file_list = []
        if user_pref_cmd in default_cmds:
            file_list = default_cmds[user_pref_cmd]()
        elif len(user_pref_cmd):
            chk_cmd = user_pref_chk
            file_list = _ls_dir(chk_cmd, user_pref_cmd)
        
        for cmd in ('rg', 'git', 'built-in'):
            if len(file_list):
                 break
            file_list = default_cmds[cmd]()
        if len(file_list) and not os.path.exists(file_list[0]):
            # relative -> fullpath
            file_list = [os.path.join(folder, f) for f in file_list]

        return [f for f in file_list if os.path.isfile(f)]

    def _grep_view_lines(self, folder, view):
        filename = view.file_name()
        rel_filename = filename.replace(folder, '')
        regions = view.find_all('.*\n')
        lines = [
            (line_no + 1, view.substr(region).strip().replace('\t', ''))
            for line_no, region in enumerate(regions)
        ]
        return [
            sublime.ListInputItem(
                text=line_str,
                value=(filename, line_no),
                annotation='%s:%s'%(rel_filename, line_no),
            ) for line_no, line_str in lines
            if len(line_str.strip()) > 0
        ]

class FuzzyActiveProjectCommand(sublime_plugin.WindowCommand):

    def run(self, file_lines, source):
        file = file_lines[0]
        line = file_lines[1]
        view = self.window.open_file(file)
        _await_view_goto_line(view, line)
        
    def input(self, args):
        print(f"args: {args}")
        # source="active_folder"
        if "source" not in args:
            args['source']="active_folder"

        if "file_lines" not in args:
            return FolderLineInputHandler(self.window, args['source'])
        else:
            return None



