import rclpy
import numpy as np
from rclpy.node import Node
import os
from geometry_msgs.msg import Pose,PoseStamped
from squaternion import Quaternion
from nav_msgs.msg import Odometry,OccupancyGrid,MapMetaData,Path
from math import pi,cos,sin
from collections import deque

# a_star 노드는 OccupancyGrid map을 받아 grid map 기반 최단경로 탐색 알고리즘을 통해 로봇이 목적지까지 가는 경로를 생성하는 노드입니다.
# 로봇의 위치(/pose), 맵(/map), 목표 위치(/goal_pose)를 받아서 전역경로(/global_path)를 만들어 줍니다. 
# goal_pose는 rviz2에서 2D Goal Pose 버튼을 누르고 위치를 찍으면 메시지가 publish 됩니다. 
# 주의할 점 : odom을 받아서 사용하는데 기존 odom 노드는 시작했을 때 로봇의 초기 위치가 x,y,heading(0,0,0) 입니다. 로봇의 초기위치를 맵 상에서 로봇의 위치와 맞춰줘야 합니다. 
# 따라서 sub2의 odom 노드를 수정해줍니다. turtlebot_status 안에는 정답데이터(절대 위치)가 있는데 그 정보를 사용해서 맵과 로봇의 좌표를 맞춰 줍니다.

# 노드 로직 순서
# 1. publisher, subscriber 만들기
# 2. 파라미터 설정
# 3. 맵 데이터 행렬로 바꾸기
# 4. 위치(x,y)를 map의 grid cell로 변환
# 5. map의 grid cell을 위치(x,y)로 변환
# 6. goal_pose 메시지 수신하여 목표 위치 설정
# 7. grid 기반 최단경로 탐색

class a_star(Node):

    def __init__(self):
        super().__init__('a_Star')
        # 로직 1. publisher, subscriber 만들기
        self.map_sub = self.create_subscription(OccupancyGrid,'map',self.map_callback,1)    # 맵 정보 메시지 수신
        self.odom_sub = self.create_subscription(Odometry,'odom',self.odom_callback,1)      # 위치 데이터 수신
        self.goal_sub = self.create_subscription(PoseStamped,'goal_pose',self.goal_callback,1)   # 목표 위치 수신
        self.a_star_pub= self.create_publisher(Path, 'global_path', 1)    # 경로 메시지 발행하기
        
        # self.global_path_msg=Path()   # 경로를 저장하기 위한 Path 메시지를 생성
        # self.final_path=[]   # 최종 경로를 저장할 빈 리스트 생성

        self.goals = []  # 여러 목적지를 저장하는 리스트
        self.current_goal_index = 0  # 현재 목적지 인덱스


        self.map_msg=OccupancyGrid()
        self.odom_msg=Odometry()
        self.is_map=False
        self.is_odom=False
        self.is_found_path=False
        self.is_grid_update=False


        # 로직 2. 파라미터 설정
        self.goal = [184,224]      # 로봇의 목표 위치
        self.map_size_x=350        # 맵의 크기
        self.map_size_y=350
        self.map_resolution=0.05   # 맵의 해상도
        self.map_offset_x=-8-8.75  # 맵의 원점 오프셋
        self.map_offset_y=-4-8.75
    
        self.GRIDSIZE=350          # 그리드의 크기를 설정

        self.dx = [-1,0,0,1,-1,-1,1,1]     # 이동 방향 리스트 (상, 하, 좌, 우, 대각선 방향)
        self.dy = [0,1,-1,0,-1,1,-1,1]
        self.dCost = [1,1,1,1,1.414,1.414,1.414,1.414]   # 이동 비용 리스트


    def grid_update(self):
        self.is_grid_update=True
        
        # 로직 3. 맵 데이터 행렬로 바꾸기
        map_to_grid = np.array(self.map_msg.data)
        self.grid = map_to_grid.reshape((self.map_size_x, self.map_size_y), order="F")  # 2차원 배열 형태로 저장
        # 여기서 reshape 할 때 맵 데이터와 self.grid의 크기 및 형태가 일치해야 함


    def pose_to_grid_cell(self,x,y):
        map_point_x = 0
        map_point_y = 0
        print("pose_to_grid_cell 왔음") 
        
        # 로직 4. 위치(x,y)를 map의 grid cell로 변환 
        # (테스트) pose가 (-8,-4)라면 맵의 중앙에 위치하게 된다. 따라서 map_point_x,y 는 map size의 절반인 (175,175)가 된다.
        # pose가 (-16.75,12.75) 라면 맵의 시작점에 위치하게 된다. 따라서 map_point_x,y는 (0,0)이 된다.
        map_point_x= int((x - self.map_offset_x) / self.map_resolution)
        map_point_y= int((y - self.map_offset_y) / self.map_resolution)
        print("pose_to_grid_cell 왔음"+str(map_point_x)+str(map_point_y)) 
        
        return map_point_x,map_point_y


    def grid_cell_to_pose(self,grid_cell):

        x = 0
        y = 0

        # 로직 5. map의 grid cell을 위치(x,y)로 변환
        # (테스트) grid cell이 (175,175)라면 맵의 중앙에 위치하게 된다. 따라서 pose로 변환하게 되면 맵의 중앙인 (-8,-4)가 된다.
        # grid cell이 (350,350)라면 맵의 제일 끝 좌측 상단에 위치하게 된다. 따라서 pose로 변환하게 되면 맵의 좌측 상단인 (0.75,6.25)가 된다.

        x = self.map_offset_x + grid_cell[0] * self.map_resolution + self.map_resolution / 2.0
        y = self.map_offset_y + grid_cell[1] * self.map_resolution + self.map_resolution / 2.0

				# grid_cell[0], grid_cell[1] : 그리드 셀의 x 및 y 좌표
				# * self.map_resolution : 맵에서의 거리로 변환
				# self.map_offset_x 및 self.map_offset_y를 더해서 맵의 시작점 고려
				# self.map_resolution / 2.0를 더하여 그리드 셀의 중심으로 위치를 조정
        
        return [x,y]


    def odom_callback(self,msg):
        self.is_odom=True    # 오도메트리 메시지를 수신했음을 나타냄
        self.odom_msg=msg    # 수신한 오도메트리 메시지를 self.odom_msg 변수에 저장


    def map_callback(self,msg):
        self.is_map=True  # 맵 메시지를 수신했음을 나타냄
        self.map_msg=msg  # 수신한 맵 메시지를 self.map_msg 변수에 저장
        

    def goal_callback(self,msg):
        
        if msg.header.frame_id=='map':     # 메시지가 'map' 프레임에 대한 정보를 포함하고 있다면 실행
            
            for pose_stamped in msg.poses:
                goal_x = pose_stamped.pose.position.x
                goal_y = pose_stamped.pose.position.y
                # x와 y 좌표를 사용하여 원하는 작업을 수행
                print(f"목적지 x: {goal_x}, y: {goal_y}")
                goal_cell = self.pose_to_grid_cell(goal_x, goal_y)  # 위치를 그리드 셀로 변환
                self.goals.append(goal_cell)
                        
                
            

            if self.is_map and self.is_odom :  # 맵 데이터와 오도메트리 데이터가 모두 수신되었는지 확인
                
                if self.is_grid_update==False :   # 그리드 맵 업데이트 여부를 확인
                    self.grid_update()    # 그리드 맵을 업데이트


                if self.current_goal_index == len(self.goals) - 1:
                    # 현재 목적지가 리스트의 마지막 목적지인 경우
                    if not self.is_found_path:

                        self.final_path=[]   # 최종 경로를 저장할 빈 리스트 생성

                        # odom_msg를 읽어 로봇 위치를 가져와서 시작위치로 지정. 
                        x=self.odom_msg.pose.pose.position.x
                        y=self.odom_msg.pose.pose.position.y
                        start_grid_cell=self.pose_to_grid_cell(x,y)


                        for i, goal in enumerate(self.goals):
                            if i == 0:
                                start_grid_cell = self.pose_to_grid_cell(x, y)
                            else:
                                start_grid_cell = self.goals[i - 1]
                                
                            # 경로 생성 및 발행
                            if self.grid[start_grid_cell[0]][start_grid_cell[1]] == 0 and self.grid[goal[0]][goal[1]] == 0 and start_grid_cell != goal:
                                self.dijkstra(start_grid_cell)  # 다익스트라 경로 계획 알고리즘을 호출
                            self.is_found_path = True


                            # 경로를 배열로 저장
                            path_as_array = []
                            for grid_cell in reversed(self.final_path):
                                waypoint_x, waypoint_y = self.grid_cell_to_pose(grid_cell)
                                path_as_array.append([waypoint_x, waypoint_y])

                            # 배열 형태로 만들어진 경로 메시지 발행
                            if len(self.final_path) != 0:
                                # 경로 메시지 발행
                                self.a_star_pub.publish(self.global_path_msg)
                                # 배열 형태로 만들어진 경로도 출력
                                print(f"Path for Goal {i + 1} (Array):", path_as_array)


                # 다음 목적지로 이동
                self.current_goal_index += 1
                self.is_found_path = False
                self.final_path = []  # 이전 목적지 경로 초기화


    def dijkstra(self,start):
        Q = deque()
        Q.append(start)
        self.cost[start[0]][start[1]] = 1
        found = False
        
        while len(Q) > 0:  # Q에 노드가 남아있는 동안 반복
            if found:
                break    

            current = Q.popleft()  # 큐에서 노드하나를 꺼내서

            for i in range(8):    # 8방향을 보고
                dx = self.dx[i]
                dy = self.dy[i]
                next_x = current[0] + dx
                next_y = current[1] + dy
        
                next = (next_x, next_y)
                if next[0] >= 0 and next[1] >= 0 and next[0] < self.GRIDSIZE and next[1] < self.GRIDSIZE:
                        if self.grid[next[0]][next[1]] < 50:
                            new_cost = self.cost[current[0]][current[1]] + self.dCost[i] # 새로운 노드의 비용을 계산하고 
                            if new_cost < self.cost[next[0]][next[1]]: # 이동하는 것이 더 저렴하다면
                                Q.append(next)    # Q에 next를 추가하고
                                self.path[next[0]][next[1]] = current   # self.path 배열에 이전 노드인 current를 저장
                                self.cost[next[0]][next[1]] = new_cost  # 비용 배열 self.cost 업데이트

        node = self.goal    # 목표 노드부터 시작
        while node != start:  # 시작 노드에 도달할 때까지
            nextNode = self.path[node[0]][node[1]]  # 현재 노드에서 이전 노드를 찾아
            self.final_path.append(node)  # final_path에 현재 노드를 추가
            node = nextNode  # 다음 노드를 현재 노드로 업데이트




        
def main(args=None):
    rclpy.init(args=args)

    global_planner = a_star()

    rclpy.spin(global_planner)


    global_planner.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()