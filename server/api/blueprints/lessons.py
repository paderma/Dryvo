import itertools
from datetime import datetime
from typing import Optional, Tuple

import flask
from flask import Blueprint
from flask_babel import gettext
from flask_login import current_user, login_required, logout_user
from loguru import logger
from sqlalchemy import and_

from server.api.blueprints import teacher_required
from server.api.database.models import (
    Lesson,
    LessonTopic,
    Place,
    PlaceType,
    Student,
    Teacher,
    Topic,
    User,
)
from server.api.push_notifications import FCM
from server.api.utils import jsonify_response, paginate
from server.consts import DATE_FORMAT, DEBUG_MODE
from server.error_handling import RouteError

lessons_routes = Blueprint("lessons", __name__, url_prefix="/lessons")


def init_app(app):
    app.register_blueprint(lessons_routes)


def handle_places(
    meetup_place: str, dropoff_place: str, student: Student
) -> Tuple[Place, Place]:
    if not student:
        return None, None
    return (
        Place.create_or_find(meetup_place, PlaceType.meetup, student),
        Place.create_or_find(dropoff_place, PlaceType.dropoff, student),
    )


def get_lesson_data(data: dict, user: User, lesson: Optional[Lesson] = None) -> dict:
    """get request data and a specific user
    - we need the user because we are not decorated in login_required here
    returns dict of new lesson or edited lesson"""
    if not data.get("date"):
        raise RouteError("Date is not valid.")
    date = datetime.strptime(data["date"], DATE_FORMAT)
    if not lesson and date < datetime.utcnow():
        # trying to add a new lesson in the past??
        raise RouteError("Date is not valid.")
    if user.student:
        duration = user.student.teacher.lesson_duration
        student = user.student
        available_hours = itertools.dropwhile(
            lambda hour_with_date: hour_with_date[0] != date,
            user.student.teacher.available_hours(date),
        )
        try:
            next(available_hours)
        except StopIteration:
            if (lesson and date != lesson.date) or not lesson:
                raise RouteError("This hour is not available.")
        teacher = user.student.teacher
    elif user.teacher:
        duration = data.get("duration", user.teacher.lesson_duration)
        teacher = user.teacher
        student = Student.get_by_id(data.get("student_id"))
        if not student:
            raise RouteError("Student does not exist.")

    meetup, dropoff = handle_places(
        data.get("meetup_place"), data.get("dropoff_place"), student
    )
    try:
        price = int(data.get("price", ""))
    except ValueError:
        price = None
    return {
        "date": date,
        "meetup_place": meetup,
        "dropoff_place": dropoff,
        "student": student,
        "teacher": teacher,
        "duration": duration,
        "price": price,
        "comments": data.get("comments"),
        "is_approved": True if user.teacher else False,
    }


@lessons_routes.route("/", methods=["GET"])
@jsonify_response
@login_required
@paginate
def lessons():
    user = current_user.teacher
    if not current_user.teacher:
        user = current_user.student

    try:
        return user.filter_lessons(flask.request.args)
    except ValueError:
        raise RouteError("Wrong parameters passed.")


@lessons_routes.route("/<int:lesson_id>", methods=["GET"])
@jsonify_response
@login_required
def lesson(lesson_id):
    lesson = Lesson.get_by_id(lesson_id)
    if not lesson:
        raise RouteError("Lesson does not exist.")

    if current_user.id not in (lesson.student.user.id, lesson.teacher.user.id):
        raise RouteError("You are not allowed to view this lesson.", 401)

    return {"data": lesson.to_dict()}


@lessons_routes.route("/", methods=["POST"])
@jsonify_response
@login_required
def new_lesson():
    data = flask.request.get_json()
    if not data.get("date"):
        raise RouteError("Please insert the date of the lesson.")
    lesson = Lesson.create(**get_lesson_data(data, current_user))

    # send fcm to the user who wasn't the one creating the lesson
    user_to_send_to = lesson.teacher.user
    body_text = gettext(
        "%(student)s wants to schedule a new lesson at %(date)s. Click here to check it out.",
        student=lesson.student.user.name,
        date=lesson.date,
    )
    if lesson.creator == lesson.teacher.user and lesson.student:
        user_to_send_to = lesson.student.user
        body_text = gettext(
            "%(teacher)s scheduled a new lesson at %(value)s. Click here to check it out.",
            teacher=lesson.teacher.user.name,
            value=lesson.date,
        )
    if user_to_send_to.firebase_token:
        logger.debug(f"sending fcm to {user_to_send_to}")
        FCM.notify(
            token=user_to_send_to.firebase_token,
            title=gettext("New Lesson!"),
            body=body_text,
        )
    return {"data": lesson.to_dict()}, 201


@lessons_routes.route("/<int:lesson_id>/topics", methods=["POST"])
@jsonify_response
@login_required
@teacher_required
def update_topics(lesson_id):
    """update or add lesson topics
    accepts {'progress': [topics in progress], 'finished': [finished topics]}"""
    data = flask.request.get_json()
    FINISHED_KEY = "finished"
    lesson = Lesson.get_by_id(lesson_id)
    if not lesson:
        raise RouteError("Lesson does not exist.")
    if not lesson.student:
        raise RouteError("Lesson must have a student assigned.")
    appended_ids = []
    for key, topic_ids in data.get("topics").items():
        for topic_id in topic_ids:
            if not Topic.get_by_id(topic_id):
                raise RouteError("Topic does not exist.")
            if topic_id in appended_ids:  # we don't want the same topic twice
                continue
            is_finished = True if key == FINISHED_KEY else False
            # existing_lesson_topic = lesson.topics.filter_by(topic_id=topic_id).first()
            # if existing_lesson_topic:
            #     if is_finished:
            #         existing_lesson_topic.update(is_finished=is_finished)
            #     continue
            lesson_topic = LessonTopic(is_finished=is_finished, topic_id=topic_id)
            lesson.topics.append(lesson_topic)
            appended_ids.append(topic_id)

    lesson.save()
    return {"data": lesson.to_dict()}, 201


@lessons_routes.route("/<int:lesson_id>", methods=["DELETE"])
@jsonify_response
@login_required
def delete_lesson(lesson_id):
    try:
        lessons = current_user.teacher.lessons
    except AttributeError:
        lessons = current_user.student.lessons
    lesson = lessons.filter_by(id=lesson_id).first()
    if not lesson:
        raise RouteError("Lesson does not exist.")

    lesson.update(deleted=True)

    user_to_send_to = lesson.teacher.user
    if current_user == lesson.teacher.user:
        user_to_send_to = lesson.student.user
    if user_to_send_to.firebase_token:
        FCM.notify(
            token=user_to_send_to.firebase_token,
            title=gettext("Lesson Deleted"),
            body=gettext(
                "The lesson at %(value)s has been deleted.", value=lesson.date
            ),
        )

    return {"message": "Lesson deleted successfully."}


@lessons_routes.route("/<int:lesson_id>", methods=["POST"])
@jsonify_response
@login_required
def update_lesson(lesson_id):
    try:
        lessons = current_user.teacher.lessons
    except AttributeError:
        lessons = current_user.student.lessons
    lesson = lessons.filter_by(id=lesson_id).first()
    if not lesson:
        raise RouteError("Lesson does not exist", 404)
    data = flask.request.get_json()
    lesson.update_only_changed_fields(
        **get_lesson_data(data, current_user, lesson=lesson)
    )

    user_to_send_to = lesson.teacher.user
    body_text = gettext(
        "%(student)s wants to edit the lesson at %(date)s. Click here to check it out.",
        student=lesson.student.user.name,
        date=lesson.date,
    )
    if current_user == lesson.teacher.user:
        user_to_send_to = lesson.student.user
        body_text = gettext(
            "%(teacher)s edited the lesson at %(value)s. Click here to check it out.",
            teacher=lesson.teacher.user.name,
            value=lesson.date,
        )
    if user_to_send_to.firebase_token:
        FCM.notify(
            token=user_to_send_to.firebase_token,
            title=gettext("Lesson Updated"),
            body=body_text,
        )

    return {"message": "Lesson updated successfully.", "data": lesson.to_dict()}


@lessons_routes.route("/<int:lesson_id>/approve", methods=["GET"])
@jsonify_response
@login_required
@teacher_required
def approve_lesson(lesson_id):
    lesson = current_user.teacher.lessons.filter_by(id=lesson_id).first()
    if not lesson:
        raise RouteError("Lesson does not exist", 404)
    # check if there isn't another lesson at the same time
    same_time_lesson = Lesson.query.filter(
        and_(
            Lesson.date == lesson.date,
            Lesson.id != lesson.id,
            Lesson.is_approved == True,
        )
    ).first()
    if same_time_lesson:
        raise RouteError("There is another lesson at the same time.")

    lesson.update(is_approved=True)

    if lesson.student.user.firebase_token:
        FCM.notify(
            token=lesson.student.user.firebase_token,
            title=gettext("Lesson Approved"),
            body=gettext("Lesson at %(date)s has been approved!", date=lesson.date),
        )

    return {"message": "Lesson approved."}


@lessons_routes.route("/payments", methods=["GET"])
@jsonify_response
@login_required
@paginate
def payments():
    """endpoint to return filtered payments"""
    user = current_user.teacher
    if not current_user.teacher:
        user = current_user.student

    try:
        return user.filter_payments(flask.request.args)
    except ValueError:
        raise RouteError("Wrong parameters passed.")


@lessons_routes.route("/<int:lesson_id>/topics", methods=["GET"])
@jsonify_response
@login_required
def topics(lesson_id: int):
    """return all available topics of a lesson -
    1. topics that fit its number
    2. topics in progress of the lesson's student
    3. topics that were picked in this lesson"""
    student = Student.query.filter_by(id=flask.request.args.get("student_id")).first()
    lesson = None
    if lesson_id == 0 and student:
        # lesson hasn't been created yet, let's treat this like a new lesson
        lesson_number = student.new_lesson_number
    else:
        lesson = Lesson.query.filter_by(id=lesson_id).first()
        if not lesson or not lesson.student:
            raise RouteError("Lesson does not exist or not assigned.", 404)
        (student, lesson_number) = (lesson.student, lesson.lesson_number)

    topics_for_lesson = student.topics(is_finished=False).union(
        set(Topic.for_lesson(lesson_number))
    ) - student.topics(is_finished=True)

    in_progress: list = []
    finished_in_this_lesson: list = []
    if lesson:
        in_progress = [
            lt.topic_id for lt in lesson.topics.filter_by(is_finished=False).all()
        ]
        finished_in_this_lesson = [
            lt.topic_id for lt in lesson.topics.filter_by(is_finished=True).all()
        ]
        # available lessons don't include student's finished topics,
        # so we have to add this specific lesson finished topics
        topics_for_lesson = topics_for_lesson.union(
            {Topic.query.filter_by(id=t).first() for t in finished_in_this_lesson}
        )
    return dict(
        available=[t.to_dict() for t in topics_for_lesson],
        progress=in_progress,
        finished=finished_in_this_lesson,
    )
