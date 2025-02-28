from fastapi import UploadFile, HTTPException, Depends, BackgroundTasks
from fastapi.responses import FileResponse
from typing import List
from models.pydantic_models import AudioVideoFileRequest
from services import file_processing_service, elastic_service
from utils.mail_utils import send_email
from starlette.concurrency import run_in_threadpool

import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FileController:
    def __init__(self):
        self.file_service = file_processing_service
        self.elastic_service = elastic_service
    
    async def get_files(self, workspace_name: str):
        """
        Retrieves a list of files from the specified workspace index in Elasticsearch.

        :param workspace_name: The name of the workspace, which is also the index name in Elasticsearch.
        :return: List of files in the specified workspace.
        """
        try:
            # Perform an Elasticsearch search query on the specified workspace index
            query = {
                "query": {
                    "match_all": {}
                }
            }
            
            # Query Elasticsearch for all documents in the workspace index
            response = await self.elastic_service.es.search(index=workspace_name, body=query)

            # Check if we got results
            if response['hits']['total']['value'] > 0:
                files = []
                for hit in response['hits']['hits']:
                    # Extract file details
                    file_data = hit["_source"]
                    files.append({
                        "filename": file_data["filename"],
                        "file_id": file_data["file_id"]
                    })
                return files
            else:
                return []  # Return an empty list if no files are found
        except Exception as e:
            print(f"Error fetching files from workspace '{workspace_name}': {e}")
            return []
    
    async def process_file(self, background_tasks: BackgroundTasks, files: List[UploadFile], body: AudioVideoFileRequest = Depends(), ):
        
        try:
            results = []
            for file in files:
                file_extension = os.path.splitext(file.filename)[1].lower()
                logger.info(f"Received file extension: {file_extension}")
                
                # Validate file type based on content type or extension 
                valid_audio_video_extensions = [".mp3", ".mp4", ".avi"]

                if file_extension in valid_audio_video_extensions:
                    logger.info(f"Processing media file {file.filename}")
                    media_results = await self.file_service.process_audio_video_files(file)
                    
                elif file_extension == ".pdf":
                    pass
                else:
                    raise HTTPException(status_code=400, detail="Unsupported file type.")  
                
                # Store Transcript in Elasticsearch
                file_id = await self.elastic_service.store_in_elastic(
                    workspace_name=body.workspace_name,
                    filename=file.filename,
                    transcript=media_results.get("transcript",""),
                    participants=body.participants,
                    transcript_embeddings=media_results.get("embeddings",[])
                )

                background_tasks.add_task(self.schedule_mail, workspace_name=body.workspace_name, file_id=file_id, summary=media_results.get("summary",""))
                
                results.append({
                    "filename": file.filename,
                    "summary": media_results.get("summary",""),
                    "file_id": file_id
                })
            return results
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error storing data in Elasticsearch: {str(e)}")
    
    async def schedule_mail(self,workspace_name: str, file_id: str, summary: str):
        try:
            logger.info(f"Scheduling email for {file_id} via gmail.")
            await run_in_threadpool(send_email, workspace_name, file_id, summary, self.elastic_service)
            logger.info("Email successfully sent.")
            return {"message": "Email Scheduled to send successfully"}
        except Exception as e:
            logger.error(f"Failed to send email for file_id: {file_id}. Error: {str(e)}")

file_controller = FileController()
