"""
Define the controller of the Bot itself.

The controller provide a full command line interface for administration:
    - dynamic plugins
    - IRC interactions
    - sudoers management

"""


import threading
import cmd
import re
from munin import config
# integrated command line
from prompt_toolkit.shortcuts import get_input
from prompt_toolkit.contrib.regular_languages.compiler import compile as pt_compile


LOGGER = config.logger()
# list of reusable subcommands names
COMMAND_PLUGINS_ADD = ('add', 'a', 'activate')
COMMAND_PLUGINS_DEL = ('deactivate', 'del', 'd', 'rm', 'r')
COMMAND_PLUGINS_LS  = ('ls', 'l')
COMMAND_PLUGINS_PRT = ('p', 'print')
COMMAND_PLUGINS_RLD = ('reload', 'rl', 'rld')
COMMAND_SUDO_ADD    = ('a', 'add')
COMMAND_SUDO_DEL    = ('d', 'del', 'rm')
# all commands, subcommands and other regex in a main dict
COMMAND_NAMES = {
    'quit'    : ('q', 'quit', ':q', 'exit'),
    'plugins' : ('plugins', 'plugin', 'pg', 'pl', 'plg'),
    'sudoers' : ('sudoers', 'sudoer', 'sudo', 'su', 'sd'),
    'irc'     : ('irc', 'lastwords', 'words', 'last', 'lw', 'w', 'wl'),
    'say'     : ('say',),
    'subsudo' : COMMAND_SUDO_ADD + COMMAND_SUDO_DEL,
    'subpgarg': COMMAND_PLUGINS_ADD + COMMAND_PLUGINS_DEL,
    'subpgnoa': COMMAND_PLUGINS_PRT + COMMAND_PLUGINS_LS,
    'subsudos': COMMAND_SUDO_ADD + COMMAND_SUDO_DEL,
    'args'    : ('.*',),
    'nick'    : ('[a-zA-Z0-9_-]+',),
    'help'    : ('help', 'h',),
    'save'    : ('save', 'save data', 'datasave',),
    'operate' : ('op', 'operate',),  # debug command
    'debug'   : ('debug', 'dbg',),  # debug command
}
# printings values
PRINTINGS_PLUGINS_MAX_WIDTH = 20
DEFAULT_INTRO  = 'Welcome to the munin shell. Type help or h to list commands.\n'
DEFAULT_PROMPT = '?>'


# COMMANDS
def commands_grammar():
    """Return a grammar for COMMAND_NAMES values."""
    def cmd2reg(cmd, subcmd=None, args=None):
        """layout automatization"""
        return (
            '(\s*  (?P<cmd>(' + '|'.join(COMMAND_NAMES[cmd]) + '))'
            + ('' if subcmd is None
               else ('\s+  (?P<subcmd>('+'|'.join(COMMAND_NAMES[subcmd]) + '))   \s*  '))
            + ('' if args   is None
               else ('\s+  (?P<args>('  +'|'.join(COMMAND_NAMES[args  ]) + '))   \s*  '))
            + ') |\n'
        )
    # get grammar, log it and return it
    grammar = (
          cmd2reg('quit'   , None      , None  )
        + cmd2reg('plugins', 'subpgnoa', None  )
        + cmd2reg('plugins', 'subpgarg', 'args')
        + cmd2reg('sudoers', 'subsudos', 'nick')
        + cmd2reg('irc'    , None      , 'args')
        + cmd2reg('say'    , None      , 'args')
        + cmd2reg('help'   , None      , None  )
        + cmd2reg('operate', None      , None  )
        + cmd2reg('save'   , None      , None  )
        + cmd2reg('debug'  , None      , None  )
    )
    LOGGER.debug('GRAMMAR:\n' + grammar)
    return pt_compile(grammar)


#########################
# CONTROL CLASS         #
#########################
class Control():
    """
    Control a Bot, as defined in bot.py file.
    Allow user to type its commands and use Control instance
    as an IRC client.
    """


# CONSTRUCTOR #################################################################
    def __init__(self, bot, prompt=DEFAULT_PROMPT, intro=DEFAULT_INTRO):
        self.bot, self.finished = bot, False

        # launch bot as thread
        self.bot_thread = threading.Thread(target=self.bot.start)
        self.bot_thread.start()

        # Initial plugins
        self.available_plugins = tuple(p(bot) for p in config.import_plugins())

        # Add whitelisted plugins automatically # TODO
        for plugin in self.available_plugins:
            plugin.load_persistent_data()
            self.bot.add_plugin(plugin)
            LOGGER.info('PLUGIN LOADED: ' + str(plugin))
        assert all(self.active(f) for f in self.available_plugins)

        # main loop control
        LOGGER.info('Connected !')
        print(intro, end='')
        grammar = commands_grammar()
        while not self.finished:
            try:
                text  = get_input(prompt)
            except (EOFError, KeyboardInterrupt):
                self.__disconnect()
                continue
            match = grammar.match(text)
            if match is not None:
                values = match.variables()
                cmd    = values.get('cmd')
                subcmd = values.get('subcmd')
                args   = values.get('args')
                LOGGER.debug('LINE:' + str(cmd) + str(subcmd) + str(args))
                if cmd in COMMAND_NAMES['plugins']:
                    self.__plugins(subcmd, args)
                elif cmd in COMMAND_NAMES['quit']:
                    self.__disconnect()
                elif cmd in COMMAND_NAMES['say']:
                    self.__say(args)
                elif cmd in COMMAND_NAMES['irc']:
                    self.__last_words(args)
                elif cmd in COMMAND_NAMES['help']:
                    self.__help()
                elif cmd in COMMAND_NAMES['operate']:
                    self.__operate()
                elif cmd in COMMAND_NAMES['sudoers']:
                    self.__sudoers(subcmd, args)
                elif cmd in COMMAND_NAMES['debug']:
                    self.__debug()
                elif cmd in COMMAND_NAMES['save']:
                    self.__save_persistant_data()
            else:
                print('not a valid command')

        # finalize all treatments
        LOGGER.info('Disconnected !')
        # self.bot_thread.join()  # warning: wait forever


# PUBLIC METHODS ##############################################################
# PRIVATE METHODS #############################################################
    def __save_persistant_data(self):
        for plugin in self.available_plugins:
            plugin.save_persistent_data()

    def __disconnect(self):
        """save persistant data, and disconnect and quit all connections"""
        self.__save_persistant_data()
        self.finished = True
        self.bot.disconnect()

    def __say(self, regex_result):
        """print message in canal"""
        self.bot.send_message(regex_result)
        LOGGER.debug('Said:' + str(regex_result))

    def __plugins(self, subcmd, values):
        """management of plugins"""
        error_code = {
            'name'  : 'need a valid plugin name',
            'id'    : 'need a valid plugin index',
        }
        error = None

        # listing
        if subcmd in COMMAND_PLUGINS_LS:
            assert(values is None)
            # each plugin will be shown with a [ACTIVATED] flag
            #  if already present of munin
            activated_flag = '\t\t[ACTIVATED]'
            plugins = (str(p) + (activated_flag if self.active(p) else '')
                       for p in self.available_plugins)
            print('\n'.join(plugins))
        # printing
        if subcmd in COMMAND_PLUGINS_PRT:
            assert(values is None)
            for fn in (str(f) for f in self.bot.plugins):
                print(str(fn))
        # activation
        elif subcmd in COMMAND_PLUGINS_ADD:
            assert(values is not None)
            values = set(int(_) for _ in values.split())
            if len(values) > 0:
                requesteds = (p for p in self.available_plugins if p.id in values)
                for requested in requesteds:
                    if not self.bot.has_plugin(requested):
                        self.bot.add_plugin(requested)
                    else:
                        print('PLUGINS: already active: ' + str(requested))
            else:
                error = 'name'
        # deactivation
        elif subcmd in COMMAND_PLUGINS_DEL:
            assert(values is not None)
            if len(values) > 0:
                try:
                    for idx in (int(_) for _ in values):
                        if not self.bot.del_plugin(idx=idx):
                            print(str(idx) + ' not found !')
                except ValueError:
                    error = 'id'
            else:
                error = 'id'
        # error output
        if error is not None:
            print('ERROR:', error_code[error])


    def __sudoers(self, subcmd, args):
        """Manage sudoers"""
        if subcmd in COMMAND_SUDO_ADD:
            self.bot.add_sudoer(args)
            print('New sudo:', args)
        elif subcmd in COMMAND_SUDO_DEL:
            self.bot.rmv_sudoer(args)
            print(args, 'is no longer a sudoer')
        print('Sudoers: ', self.bot.sudoers)


    def __last_words(self, args):
        """Show last words on channel"""
        args = int(args)
        if args > 0:
            raise NotImplementedError
        else:
            print('ERROR:', args, 'is not a valid number of message to display')


    def __help(self):
        print('This is the help: «good luck»')  # TODO: do something useful


    def __operate(self):
        command = int(input("""\t0: send gold to...\n\t1: take gold of..."""
                            """\t\t2: take n gold of..."""
                            """\n\t3: send n gold to many..."""
                            """\t4: create a new gold for..."""
                            """\n\t5: clear unused names."""
                            """\t\t6: generate graph."""))
        gold_manager = next(plugin for plugin in self.bot.plugins
                            if 'GoldManager' in plugin.__class__.__name__)
        if command == 0:
            receiver = input('Send gold to: ')
            gold_manager.give_gold(receiver)
            print('Gold added to', receiver)
        elif command == 1:
            donator = input('Take one gold of: ')
            receiver = input('give it to: ')
            gold_manager.give_gold(receiver, donator=donator)
            print('Gold added to', receiver)
        elif command == 2:
            donator = input('Take gold of: ')
            receiver = input('give it to: ')
            nb_gold = int(input('nb_gold: '))
            for _ in range(nb_gold):
                gold_manager.give_gold(receiver, donator=donator)
                print('Gold added to', receiver)
        elif command == 3:
            nb_gold = int(input('nb_gold per name: '))
            receivers = input('give it to (comma sep): ').split(',')
            for receiver in receivers:
                for _ in range(nb_gold):
                    gold_manager.give_gold(receiver)
                    print('Gold added to', receiver)
        elif command == 4:
            nb_gold = int(input('nb gold: '))
            receiver = input('give it to: ')
            gold_manager.create_gold_for(receiver, nb_gold)
            print(nb_gold, 'given to ' + receiver + '.')

        elif command == 5:
            gold_manager.clean_unused_names()
            print('Unused names cleaned !')

        elif command == 6:
            output_filename = input('output filename: ')
            gold_manager.save_graph(output_filename)

        else:
            print('w00t ?')

    def __debug(self):
        for plugin in self.available_plugins:
            print('\n' + str(plugin), plugin.__class__.__name__)
            print('\n\t', plugin.debug_data)


    def active(self, plugin):
        """True if given plugin is active, ie is referenced by bot"""
        assert plugin in self.available_plugins
        return self.bot.has_plugin(plugin)
