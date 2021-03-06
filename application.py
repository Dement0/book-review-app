#! /usr/bin/python
import os
import requests

from flask import Flask, flash, jsonify, render_template, redirect, request, session, url_for
from flask_session import Session
from tempfile import mkdtemp
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.exceptions import default_exceptions
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import login_required, get_book, userHasCommented, get_reviews, get_review_stats


app = Flask(__name__)

# Check for environment variable for database
if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is not set")

# Check for environment varible for Goodreads API
if not os.getenv("GOODREADS_KEY"):
    raise RuntimeError("GOODREADS_KEY is not set")

# Configure session to use filesystem
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database
engine = create_engine(os.getenv("DATABASE_URL"))
db = scoped_session(sessionmaker(bind=engine))

# Ask for Goodreads Key
gr = os.getenv("GOODREADS_KEY")
goodreads_key = scoped_session(sessionmaker(bind=gr))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Register user"""

    # User reached route via POST
    if request.method == 'POST':

        # Ensure username was submitted
        if not request.form.get('username'):
            flash('You must provide a username.')
            return redirect(url_for('register'))

        # Ensure password was submitted
        if not request.form.get('password'):
            flash('You must provide a password.')
            return redirect(url_for('register'))

        # Query database for username
        username = request.form.get('username')
        rows = db.execute('SELECT * FROM users WHERE username = :username', {"username": username}).fetchall()
        # Check if it exists
        if len(rows) != 0:
            flash('This username exists.')
            return redirect(url_for('register'))
        # Insert values provided to database
        password = generate_password_hash(request.form.get('password'))
        db.execute('INSERT INTO users (username, password) VALUES (:username, :password)', {"username": username, "password": password})
        print('User has been registered correctly.')
        # Commit changes
        db.commit()
        # Close connection
        db.close()

        # Remember username for that session
        session['username'] = request.form['username']
        flash('Successfully registered.')
        return redirect(url_for('login'))

    # User reached route via GET
    else:
        return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Log user in"""

    # Forget any username
    session.clear()

    # User has reached via POST
    if request.method == 'POST':

        # Ensure username was submitted
        if not request.form.get('username'):
            flash('You must provide a username.')
            return redirect(url_for('login'))

        # Ensure password was submitted
        if not request.form.get('password'):
            flash('You must provide a password.')
            return redirect(url_for('login'))

        # Query db for that username
        username = request.form.get('username')
        rows = db.execute('SELECT * FROM users WHERE username = :username', {"username": username}).fetchall()

        # Check if rows has some data
        if len(rows) == 0:
            flash('No such username.')
            return redirect(url_for('login'))

        # Ensure username exists and password is correct
        if len(rows) != 1 or not check_password_hash(rows[0][2], request.form.get('password')):
            flash('Wrong password.')
            return redirect(url_for('login'))

        # Remember user id for that session
        session['user_id'] = rows[0]['user_id']

        # Redirect with success message
        flash('Successfully logged in!')
        return redirect(url_for('search'))

    # User reached route via GET
    else:
        return render_template('login.html')


@app.route('/logout')
def logout():
    """Log user out"""

    # Forget any username
    session.clear()

    # Redirect to index page
    flash("Successfully logged out!")
    return redirect(url_for('index'))


@app.route('/search', methods=['GET', 'POST'])
@login_required
def search():
    """Enable the user to search ISBN, title or author of a book and show results."""

    # User reached route via POST
    if request.method == 'POST':
        isbnQuery = request.form.get('isbnQuery')
        titleQuery = request.form.get('titleQuery')
        authorQuery = request.form.get('authorQuery')

        # Check at least user provides a field for query
        if not isbnQuery and not titleQuery and not authorQuery:
            flash("You should provide at least 1 field.")
            return redirect(url_for('search'))

        # Added '%' wildcards for PostgreSQL ILIKE pattern matching
        # Additional info on: https://www.postgresql.org/docs/9.3/static/functions-matching.html
        if isbnQuery:
            isbnQuery = '%' + isbnQuery + '%'

        if titleQuery:
            titleQuery = '%' + titleQuery + '%'

        if authorQuery:
            authorQuery = '%' + authorQuery + '%'

        # Query db
        rows = db.execute('SELECT * FROM books WHERE isbn ILIKE :isbn OR \
                           author ILIKE :author OR title ILIKE :title LIMIT \
                           10', {"isbn": isbnQuery, "author": authorQuery, \
                           "title": titleQuery}).fetchall()

        # Check if we have any match, if not flash and redirect to search page
        if len(rows) is 0:
            flash("No match. Please search again.")
            return redirect(url_for('search'))

        # Declare a list and for each row append it to that list as a dict object
        bookQueryResults = []
        for row in rows:
            bookQueryResults.append({'isbnResult': row.isbn, 'titleResult': row.title, 'authorResult': row.author})

        return render_template('search.html', bookQueryResults = bookQueryResults)

    # User reached route via GET
    else:
        return render_template('search.html')


@app.route('/book/<isbn_num>', methods=['GET', 'POST'])
def book(isbn_num):
    # User reached route via POST
    if request.method == 'POST':

        # Use helper function `get_book()` to store results in book
        book = get_book(isbn_num)

        # Use Goodreads API https://www.goodreads.com/api
        goodreads_response = requests.get("https://www.goodreads.com/book/review_counts.json", params={"key": goodreads_key, "isbns": isbn_num})
        goodreads_json = (goodreads_response.json()['books'][0])

        # Use helper function `get_reviews()` to store review results of that book
        reviews = get_reviews(isbn_num)

        # Get corresponding fields from 'search form'
        numRating = request.form.get('reviewStar')
        textRating = request.form.get('reviewText')
        if (numRating is not None or textRating is not None):
            # If user has not submitted a number rating, assign 5 as default
            if numRating is '':
                numRating = 5
                numRating = int(numRating)
            else:
                numRating = int(numRating)

            # Verify that user has logged in
            if (session['user_id'] is None):
                print("You have to log in to submit a review")
                return redirect(url_for('login'))
            else:
                user_id = session['user_id']

            # Check user_id from the database to ensure that user has not submitted another review for that book
            hasCommented = userHasCommented(user_id, isbn_num)
            if (hasCommented is True):
                flash("You have already submitted a comment for that book!")
                return redirect(url_for('search'))
            else:
                # Insert into database
                comment = db.execute('INSERT INTO reviews (isbn, user_id, rating, text_review) VALUES (:isbn, :user_id, :rating, :text_review)', {"isbn": isbn_num, "user_id": user_id, "rating": numRating, "text_review": textRating})
                db.commit()
                db.close()
                # Give feedback to user
                flash("Submitted your comment!")
        return render_template('book.html', isbn_num=isbn_num, book=book, book_json=goodreads_json, reviews=reviews)
    else:
        flash("GET Method Not Allowed!")
        return redirect(url_for('search'))


@app.route('/developer')
def developer():
    return render_template('developer.html')


@login_required
@app.route('/api/<isbn>')
def api(isbn):
    # Use helper functions to get info for building response
    book = get_book(isbn)
    reviews = get_review_stats(isbn)
    print(reviews)
    # Store it in dict object
    api_response = {
        "isbn": book['isbn'],
        "title": book['title'],
        "author": book['author'],
        "year": book['year']
    }
    if reviews is not None:
        api_response['review_count'] = reviews['review_count']
        api_response['average_score'] =  reviews['average_score']
    # Convert it to JSON
    api_response = jsonify(api_response)

    return api_response
