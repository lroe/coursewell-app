document.addEventListener('DOMContentLoaded', () => {
    const chatBox = document.getElementById('chat-box');
    const inputArea = document.getElementById('input-area');
    const systemMessage = document.getElementById('system-message');
    
    // Get the Q&A input elements
    const qnaInput = document.getElementById('qna-input');
    const sendQnaBtn = document.getElementById('send-qna-btn');

    let currentStep = 0;
    let isWaitingForResponse = false;
    let chatHistory = [];

    // --- Q&A Input Logic ---
    function sendQuestion() {
        if (isWaitingForResponse) return;
        const question = qnaInput.value.trim();
        if (question !== "") {
            addMessage(question, 'student');
            updateHistory('user', question);
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

    // --- Message and History Functions ---
    function addMessage(text, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;
        messageDiv.innerHTML = text.replace(/\n/g, '<br>');
        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function updateHistory(role, text) {
        chatHistory.push({ role: role, parts: [{ text: text }] });
    }

    function addMediaMessage(url, alt) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message tutor-message media-message';
        const img = document.createElement('img');
        img.src = url;
        img.alt = alt;
        messageDiv.appendChild(img);
        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    // --- Dynamic UI Rendering ---
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
                updateHistory('user', `Selected answer: ${answerText}`);
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
        submitButton.className = 'btn btn-primary';
        submitButton.innerText = 'Submit Answer';
        submitButton.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            const answer = answerTextarea.value.trim();
            if (answer === "") { alert("Please type an answer."); return; }
            addMessage(answer, 'student');
            updateHistory('user', answer);
            postToChat(answer, 'LESSON_FLOW');
        });
        inputArea.appendChild(submitButton);
    }

    // --- Core Chat Function ---
    async function postToChat(userInput = null, requestType = 'LESSON_FLOW') {
        isWaitingForResponse = true;
        systemMessage.innerText = 'Guidee is thinking...';
        systemMessage.style.display = 'block';
        qnaInput.disabled = true;
        sendQnaBtn.disabled = true;
        
        // Always clear the dynamic input area before a new turn
        inputArea.innerHTML = ''; 

        const requestBody = {
            lesson_id: LESSON_ID,
            step_index: currentStep,
            user_input: userInput,
            chat_history: chatHistory,
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

        if (data.feedback) { addMessage(data.feedback, 'tutor'); updateHistory('model', data.feedback); }
        if (data.media_url) { addMediaMessage(data.media_url, "Lesson media"); }
        if (data.tutor_text) { addMessage(data.tutor_text, 'tutor'); updateHistory('model', data.tutor_text); }
        
        if (Object.keys(data).length === 1 && data.next_step) {
             currentStep = data.next_step;
             postToChat(null, 'LESSON_FLOW');
             return;
        }

        if (data.is_qna_response) {
             // After a Q&A, we now explicitly show the continue button
             // so the user can resume the lesson flow.
             const continueButton = document.createElement('button');
             continueButton.innerText = 'Continue';
             continueButton.className = 'btn btn-primary'; 
             continueButton.addEventListener('click', () => {
                 if (isWaitingForResponse) return;
                 addMessage('Continue', 'student');
                 updateHistory('user', 'Continue');
                 postToChat('Continue', 'LESSON_FLOW');
             });
             inputArea.appendChild(continueButton);
             return; 
        }

        let nextInteractionScheduled = false;
        if (data.is_lesson_end) {
            if (data.next_chapter_url) {
                const nextChapterButton = document.createElement('a');
                nextChapterButton.href = data.next_chapter_url;
                nextChapterButton.innerText = 'Go to Next Chapter';
                nextChapterButton.className = 'btn btn-primary';
                inputArea.appendChild(nextChapterButton);
            }
            nextInteractionScheduled = true;
        } else if (data.question) {
            if (data.question.type === 'QUESTION_MCQ') {
                showMCQOptions(data.question);
            } else if (data.question.type === 'QUESTION_SA') {
                showShortAnswerInput(data.question);
            }
            nextInteractionScheduled = true;
        }

        // If no other button was scheduled, it means we just delivered content and should
        // now show the "Continue" button in the designated input area.
        // REPLACE WITH THIS BLOCK
        if (!nextInteractionScheduled) {
            // Create a container div that will align its content to the right
            const buttonContainer = document.createElement('div');
            buttonContainer.style.textAlign = 'right';

            const continueButton = document.createElement('button');
            continueButton.innerText = 'Continue';
            continueButton.className = 'btn btn-primary';
            // REMOVED: continueButton.style.width = '100%';

            continueButton.addEventListener('click', () => {
                if (isWaitingForResponse) return;
                addMessage('Continue', 'student');
                updateHistory('user', 'Continue');
                postToChat('Continue', 'LESSON_FLOW');
            });

            // Add the button to the container, and the container to the input area
            buttonContainer.appendChild(continueButton);
            inputArea.appendChild(buttonContainer);
        }
        
        currentStep = data.next_step;
    }

    // Initial call to start the lesson
    postToChat(null, 'LESSON_FLOW');
});