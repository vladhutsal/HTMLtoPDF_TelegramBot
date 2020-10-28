#!/usr/bin/env python3

import telegram
import logging
import tempfile

from credentials import token
from Proposal import Proposal
from ProposalDBHandler import ProposalDBHandler

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton)

from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackQueryHandler)

logging.getLogger('apscheduler.scheduler').propagate = False
db_handler = ProposalDBHandler()
proposal = Proposal(db_handler)

# set pattern constatnts

# States for ConversationHandler:
STORE_DATA, SELECT_ACTION = map(chr, range(2))
# Templates constatns:
CREATE_PROPOSAL, ADD_INFO, ADD_NEW_ENGINEER, ADD_ENGINEERS_RATE,  = map(chr, range(3, 7))
# States of the proposal fill process, to show in what state bot is right now:
CHOOSE_TITLE_TO_EDIT, EDIT_TITLE, CHOOSE_ENGINEER, OVERVIEW, INIT_TEMP, CREATE_PDF, TEST = map(chr, range(8, 15))

templates = {
    CREATE_PROPOSAL:    proposal.content_dict,
    ADD_INFO:           proposal.info_dict,
    ADD_NEW_ENGINEER:   proposal.engineer_dict,
    ADD_ENGINEERS_RATE: db_handler.engineers_rates
}


def start(update, context):

    # reset content dict when restarting proposal
    context.user_data['chat_id'] = update.message.chat_id
    context.user_data['test'] = False

    buttons = [[
        InlineKeyboardButton(text='Create new proposal',
                             callback_data=f'{CREATE_PROPOSAL}, {INIT_TEMP}'),
        InlineKeyboardButton(text='Test',
                             callback_data=str(TEST))
    ]]

    keyboard = InlineKeyboardMarkup(buttons)
    update.message.reply_text('Hi, I`ll help you to complete the proposal.')
    update.message.reply_text(text='What do you want to do?',
                              reply_markup=keyboard)

    return SELECT_ACTION


def initialize_template(update, context):
    query = update.callback_query

    template = detach_id_from_callback(query.data)
    proposal.current_template = template
    proposal.current_dict = templates[template]
    proposal.reset_iter()
    query.answer()

    return next_title(update, context)


# ================ FILL TEMPLATES WITH DATA
def next_title(update, context):
    try:
        proposal.current_title_id = proposal.get_next_title_id()
        return show_title(update, context)
    except StopIteration:
        if proposal.current_template == ADD_NEW_ENGINEER:

            # telegram_bot.py is no need to know about db_handler class
            err = db_handler.store_new_engineer_to_db(proposal.current_dict)
            proposal.reset_engineer_dict()
            if err:
                show_error_message(update, context)

        return overview(update, context)


def show_title(update, context):
    title_id = proposal.current_title_id
    title_name = proposal.get_bold_title(title_id)
    context.bot.send_message(chat_id=context.user_data['chat_id'],
                             text=title_name,
                             parse_mode=telegram.ParseMode.HTML)
    return STORE_DATA


def store_photo(update, context):
    photo_info = update.message.photo[-1]
    file_id = photo_info.file_id
    File_obj = context.bot.get_file(file_id=file_id)

    dir_path = 'engineers_photo/'
    name = proposal.get_random_name()
    photo_path = f'{dir_path}{name}.jpg'
    save_path = f'"../{dir_path}{name}.jpg"'
    File_obj.download(custom_path=photo_path)
    proposal.store_content(save_path)

    return next_title(update, context)


def store_data(update, context):
    user_content = update.message.text
    proposal.store_content(user_content)

    if proposal.edit_all:
        return next_title(update, context)

    elif not proposal.edit_all:
        proposal.edit_all = True
        if proposal.add_rate:
            proposal.add_rate = False
            return choose_engineers(update, context)

        return overview(update, context)


# ================ EDIT AND OVERVIEW
# make overview more flexible, eg for engineers overview
def overview(update, context):
    context.bot.send_message(chat_id=context.user_data['chat_id'],
                             text='Info you`ve provided:')
    for title_id in proposal.current_dict.keys():
        title = proposal.get_bold_title(title_id)
        content = proposal.get_title_content(title_id)
        context.bot.send_message(chat_id=context.user_data['chat_id'],
                                 text=f'{title}\n{content}',
                                 parse_mode=telegram.ParseMode.HTML)

    if proposal.current_template == CREATE_PROPOSAL:
        text = 'Add info'
        callback_data = f'{ADD_INFO}, {INIT_TEMP}'
    else:
        text = 'Choose engineers'
        callback_data = CHOOSE_ENGINEER

    buttons = [[
        InlineKeyboardButton(text=text,
                             callback_data=callback_data),
        InlineKeyboardButton(text='Edit',
                             callback_data=str(CHOOSE_TITLE_TO_EDIT))
    ]]

    keyboard = InlineKeyboardMarkup(buttons)
    context.bot.send_message(chat_id=context.user_data['chat_id'],
                             text='All good',
                             reply_markup=keyboard)
    return SELECT_ACTION


def choose_title_to_edit(update, context):
    query = update.callback_query
    current_dict = proposal.current_dict

    buttons = []
    for title_id in proposal.current_dict.keys():
        btn = [InlineKeyboardButton(text=f'{current_dict[title_id][0]}',
                                    callback_data=f'{title_id}, {EDIT_TITLE}')]
        buttons.append(btn)

    buttons = add_button('<< Go back', OVERVIEW, buttons)

    keyboard = InlineKeyboardMarkup(buttons)
    query.edit_message_text(text='Choose a title you want to edit:',
                            reply_markup=keyboard)
    query.answer()

    return SELECT_ACTION


def edit_title(update, context):
    query = update.callback_query
    proposal.current_title_id = detach_id_from_callback(query.data)

    if ADD_ENGINEERS_RATE in query.data:
        add_engineer_to_proposal()
        proposal.current_dict = db_handler.engineers_rates
    proposal.edit_all = False

    return show_title(update, context)


# ================ ENGINEERS
def choose_engineers(update, context):
    query = update.callback_query
    engineers = db_handler.get_engineers_id_list()

    buttons = []
    if engineers:
        for engineer_id in engineers:
            engineer_name = db_handler.get_field_info(engineer_id, 'N')
            if engineer_id not in db_handler.engineers_in_proposal_id:
                buttons = add_button(engineer_name,
                                     f'{engineer_id}, {ADD_ENGINEERS_RATE}, {EDIT_TITLE}',
                                     buttons)

    help_btns = [
        InlineKeyboardButton(text='Add new engineer',
                             callback_data=f'{ADD_NEW_ENGINEER}, {INIT_TEMP}'),
        InlineKeyboardButton(text='Continue',
                             callback_data=CREATE_PDF)
    ]
    buttons.append(help_btns)

    keyboard = InlineKeyboardMarkup(buttons)
    context.bot.send_message(chat_id=context.user_data['chat_id'],
                             text='Choose engineers: ',
                             reply_markup=keyboard)
    if query:
        query.answer()

    return SELECT_ACTION


def add_engineer_to_proposal():
    # add engineers id to list of engineers in current proposal:
    engineer_id = proposal.current_title_id
    curr_list = db_handler.engineers_in_proposal_id
    curr_list.append(int(engineer_id))

    # add engineers id to dictionary as key {'engn_id': ['name', 'rate']}
    engineer_name = db_handler.get_field_info(engineer_id, 'N')
    db_handler.engineers_rates[engineer_id] = [f'Current rate for {engineer_name}', '']
    proposal.add_rate = True


# ================ HELPERS
def detach_id_from_callback(query_data):
    additional_query_data = query_data.split(',')[0]
    return additional_query_data


def add_button(text, callback, buttons):
    btn = [InlineKeyboardButton(text=text,
                                callback_data=callback)]
    buttons.append(btn)
    return buttons


# use callback query answer to show notification on top of the bots chat
def show_error_message(update, context):
    context.bot.send_message(chat_id=context.user_data['chat_id'],
                             text='This engineer is already in db')


def generate_tmp_files(*args):
    res = []
    for suffix in args:
        tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, dir='.')
        res.append(tmp_file)
    return res


# ================ HTML TO PDF
# how to call all next functions without update and context args?
def generate_html(update, contex):
    collected_data = proposal.collect_user_data_for_html()

    env = Environment(loader=FileSystemLoader('static/'))
    template = env.get_template('index.html')
    jinja_rendered_html = template.render(**collected_data)
    proposal.html = generate_tmp_files('.html')[0]

    with open(proposal.html.name, 'w+') as html:
        html.write(jinja_rendered_html)

    return generate_pdf(update, contex)


def generate_pdf(update, context):
    pdf_doc = HTML(proposal.html.name)
    pdf_doc_rndr = pdf_doc.render(stylesheets=['static/main.css'])
    page = pdf_doc_rndr.pages[0]
    child_list = [child for child in page._page_box.descendants()]

    body_height = child_list[2].height
    page.height = body_height
    proposal.pdf = generate_tmp_files('.pdf')[0]
    pdf_doc_rndr.write_pdf(proposal.pdf.name)

    update.callback_query.answer()

    return send_pdf(context, update)


def send_pdf(context, update):
    c_id = context.user_data['chat_id']

    with open(proposal.pdf.name, 'rb') as pdf:
        context.bot.send_document(chat_id=c_id, document=pdf)

    return ConversationHandler.END


# ================ GENERATE TEST PDF
def get_test_pdf_dict(update, context):
    proposal.test = True
    update.callback_query.answer()

    return generate_html(update, context)


def end():
    pass


def main():
    updater = Updater(token=token, use_context=True)
    dispatcher = updater.dispatcher
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECT_ACTION: [CallbackQueryHandler(initialize_template,
                            pattern=f'.+{INIT_TEMP}$'),


                            CallbackQueryHandler(choose_engineers,
                            pattern='^' + str(CHOOSE_ENGINEER) + '$'),


                            CallbackQueryHandler(edit_title,
                            pattern=f'.+{EDIT_TITLE}$'),

                            CallbackQueryHandler(get_test_pdf_dict,
                            pattern=TEST),

                            CallbackQueryHandler(generate_html,
                            pattern='^' + str(CREATE_PDF) + '$'),

                            CallbackQueryHandler(choose_title_to_edit,
                            pattern='^' + str(CHOOSE_TITLE_TO_EDIT) + '$'),

                            CallbackQueryHandler(overview,
                            pattern='^' + str(OVERVIEW) + '$')],

            STORE_DATA: [MessageHandler(Filters.text, store_data),
                         MessageHandler(Filters.photo, store_photo)]
        },

        fallbacks=[CommandHandler('end', end)],

        allow_reentry=True
     )

    dispatcher.add_handler(conv_handler)
    updater.start_polling()


if __name__ == "__main__":
    main()