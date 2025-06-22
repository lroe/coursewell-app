document.addEventListener('DOMContentLoaded', () => {
    const chatBox = document.getElementById('chat-box');
    const inputArea = document.getElementById('input-area');
    const systemMessage = document.getElementById('system-message');
    const qnaInput = document.getElementById('qna-input');
    const sendQnaBtn = document.getElementById('send-qna-btn');
    const resetBtn = document.getElementById('reset-btn');
    const deleteLastBtn = document.getElementById('delete-last-btn');

    let isWaitingForResponse = false;
    let localChatHistory = [];

    // --- Q&A Input Logic ---
    function sendQuestion() {
        if (isWaitingForResponse) return;
        const question = qnaInput.value.trim();
        if (question !== "") {
            addMessage(question, 'student');
            postToChat(question, 'QNA');
            qnaInput.value = '';
        }
    }

    sendQnaBtn.addEventListener('click', sendQuestion);
    qnaInput.addEventListener('keypress', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            sendQuestion();
        }
    });

    // --- Chat Control Button Logic ---
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            if (confirm('Are you sure you want to reset this entire conversation? Your progress in this chapter will be lost.')) {
                isWaitingForResponse = true;
                systemMessage.innerText = 'Resetting...';
                systemMessage.style.display = 'block';
                fetch('/chat/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lesson_id: LESSON_ID })
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        window.location.reload();
                    } else {
                        alert('Could not reset conversation.');
                        isWaitingForResponse = false;
                        systemMessage.style.display = 'none';
                    }
                });
            }
        });
    }

    if (deleteLastBtn) {
        deleteLastBtn.addEventListener('click', async () => {
            if (isWaitingForResponse) return;
            isWaitingForResponse = true;
            systemMessage.innerText = 'Deleting...';
            systemMessage.style.display = 'block';

            const response = await fetch('/chat/delete_last_turn', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lesson_id: LESSON_ID })
            });

            const data = await response.json();
            if (data.success) {
                // To properly re-render, a full reload is the simplest way
                window.location.reload();
            } else {
                alert(data.message || 'Could not delete the last turn.');
            }

            isWaitingForResponse = false;
            systemMessage.style.display = 'none';
        });
    }
    
    // --- Rendering and UI Functions ---
    function addMessage(text, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;
        
        // NEW: Parse markdown before setting innerHTML
        messageDiv.innerHTML = marked.parse(text);

        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;

        // NEW: Tell MathJax to typeset the new content
        MathJax.typesetPromise([messageDiv]).catch((err) => console.log('MathJax error:', err));
    }

    function addImageMessage(url, alt) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message tutor-message media-message';
        const img = document.createElement('img');
        img.src = url;
        img.alt = alt;
        messageDiv.appendChild(img);
        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function addAudioMessage(url, description) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message tutor-message audio-message';
        
        const desc = document.createElement('p');
        desc.style.marginBottom = '5px';
        desc.innerText = description;

        const audio = document.createElement('audio');
        audio.controls = true;
        audio.src = url;
        
        messageDiv.appendChild(desc);
        messageDiv.appendChild(audio);
        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
    }


    function showMCQOptions(questionData) {
        inputArea.innerHTML = '';
        const questionText = document.createElement('p');
        questionText.innerText = questionData.question;
        inputArea.appendChild(questionText);

        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'options-container';
        for (const key in questionData.options) {
            const button = document.createElement('button');
            button.className = 'btn btn-secondary';
            button.innerText = `${key}: ${questionData.options[key]}`;
            button.dataset.answer = key;
            button.addEventListener('click', () => {
                if (isWaitingForResponse) return;
                const answerText = `${key}: ${questionData.options[key]}`;
                addMessage(answerText, 'student');
                postToChat(key, 'LESSON_FLOW');
            });
            optionsContainer.appendChild(button);
        }
        inputArea.appendChild(optionsContainer);
    }

    function showShortAnswerInput(questionData) {
        inputArea.innerHTML = '';
        const questionText = document.createElement('p');
        questionText.innerText = questionData.question;
        inputArea.appendChild(questionText);

        const answerTextarea = document.createElement('textarea');
        answerTextarea.rows = 3;
        answerTextarea.placeholder = "Type your answer here...";
        inputArea.appendChild(answerTextarea);

        const submitButton = document.createElement('button');
        submitButton.className = 'btn';
        submitButton.innerText = 'Submit Answer';
        submitButton.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            const answer = answerTextarea.value.trim();
            if (answer === "") { alert("Please type an answer."); return; }
            addMessage(answer, 'student');
            postToChat(answer, 'LESSON_FLOW');
        });
        inputArea.appendChild(submitButton);
    }
    
    function showContinueButton() {
        inputArea.innerHTML = '';
        const buttonContainer = document.createElement('div');
        buttonContainer.style.textAlign = 'right';

        const continueButton = document.createElement('button');
        continueButton.innerText = 'Continue';
        continueButton.className = 'btn btn-primary'; 

        continueButton.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            // No need to display "Continue" in the chat, it just triggers the next step
            postToChat('Continue', 'LESSON_FLOW');
        });

        buttonContainer.appendChild(continueButton);
        inputArea.appendChild(buttonContainer);
    }

    // --- Core Chat Function ---
    async function postToChat(userInput = null, requestType = 'LESSON_FLOW') {
        isWaitingForResponse = true;
        systemMessage.innerText = 'Guidee is thinking...';
        systemMessage.style.display = 'block';
        qnaInput.disabled = true;
        sendQnaBtn.disabled = true;
        
        inputArea.innerHTML = ''; 

        const requestBody = {
            lesson_id: LESSON_ID,
            user_input: userInput,
            request_type: requestType
        };

        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        isWaitingForResponse = false;
        systemMessage.style.display = 'none';
        qnaInput.disabled = false;
        sendQnaBtn.disabled = false;
        
        // Render new messages and media from the backend
        if (data.media_url) {
            if (data.media_type === 'audio') {
                addAudioMessage(data.media_url, data.tutor_text || "Listen to this:");
            } else { // Default to image
                addImageMessage(data.media_url, "Lesson media");
            }
        }
        if (data.tutor_text && !data.media_url) { // Don't show text twice for media
             addMessage(data.tutor_text, 'tutor');
        }

        if (data.is_qna_response) {
             showContinueButton();
             return; 
        }

        if (data.is_lesson_end) {
            inputArea.innerHTML = ''; // Clear for final buttons
            if (data.certificate_url) {
                const certLink = document.createElement('a');
                certLink.href = data.certificate_url;
                certLink.innerText = 'View Your Certificate!';
                certLink.className = 'btn btn-primary';
                inputArea.appendChild(certLink);
            } else if (data.next_chapter_url) {
                const nextChapterButton = document.createElement('a');
                nextChapterButton.href = data.next_chapter_url;
                nextChapterButton.innerText = 'Go to Next Chapter';
                nextChapterButton.className = 'btn btn-primary';
                inputArea.appendChild(nextChapterButton);
            }
        } else if (data.question) {
            if (data.question.type === 'QUESTION_MCQ') {
                showMCQOptions(data.question);
            } else if (data.question.type === 'QUESTION_SA') {
                showShortAnswerInput(data.question);
            }
        } else {
            showContinueButton();
        }
    }

    // --- Initialization Logic ---
    function initializeLesson() {
        // We start the lesson by "clicking" continue with no input
        postToChat(null, 'LESSON_FLOW');
    }

    initializeLesson();
});